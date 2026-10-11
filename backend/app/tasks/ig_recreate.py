"""Mode 3 — recreate a scraped Instagram post as a designed image.

For a fanpage with ig_recreate_enabled, classify the post image via 9Router
vision (quote / news / other):
  - quote → put the extracted quote on the fanpage's QUOTE template
  - news  → AI-rewrite the extracted headline, put it on the NEWS template
  - other → skip (no job)
Both use the IG post image as the photo. The design job renders via the same
headless renderer, then publishes like a news design job.
"""

import base64
import logging
import uuid
from pathlib import Path

import httpx

from app.tasks.celery_app import celery_app
from celery.exceptions import SoftTimeLimitExceeded
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_RENDER_TIMEOUT = 120.0


import re

def _rewrite_news_title(text: str, caption: str, fanpage, other_titles: list[str] = None) -> str:
    from app.services.ai_caption import generate_caption

    def get_numbers(s: str) -> set:
        return set(re.findall(r'\d+(?:\.\d+)?', s.replace(',', '')))

    source_numbers = get_numbers(text)

    prompt = (
        f'Rewrite this news headline for the Facebook page "{fanpage.name}".\n'
        f'HEADLINE: "{text}"\n'
        f'CAPTION: "{caption}"\n'
        f"- Language: {fanpage.caption_language}\n"
        f"- Identify the single NEW fact of the story.\n"
        f"- Keep every key number/name/team exactly as in the source (prefer the source headline's figure; never drop or change totals).\n"
        f"- Do not add \"BREAKING\"/\"JUST IN\"/\"OFFICIAL\" unless the source itself says so AND the event is the new fact.\n"
        f"- No clickbait questions that hide the fact.\n"
        f"- Keep the existing **keyword** highlight markers for the main figure/name.\n"
        f"- Max {fanpage.mode2_title_max_chars or 80} characters.\n"
    )
    if other_titles:
        prompt += "- Make it different from these (do not reuse their exact phrasing):\n"
        for t in other_titles:
            prompt += f"  * {t}\n"

    prompt += "Output ONLY the rewritten headline — no quotes, no explanation."
    
    title, _ = generate_caption(prompt)
    title = title.strip().strip('"')

    if not source_numbers.issubset(get_numbers(title)):
        retry_prompt = prompt + f"\n\nERROR: You must include these exact numbers from the source headline: {', '.join(source_numbers)}\nOutput the corrected headline ONLY."
        title, _ = generate_caption(retry_prompt)
        title = title.strip().strip('"')
        if not source_numbers.issubset(get_numbers(title)):
            title = text.replace('**', '').replace('\n', ' ').strip()

    return title


def _translate_quote(text: str, fanpage) -> str:
    """Quote extraction deliberately keeps the source language (see
    ig_content_classifier._PROMPT) so it isn't paraphrased before we even know
    it's a quote. This step then translates it to the fanpage's configured
    language — mirroring _rewrite_news_title above — while preserving meaning
    and attribution exactly, since a quote must stay accurate (unlike a
    headline, which is allowed to be rewritten for punch)."""
    from app.services.ai_caption import generate_caption

    if not fanpage.caption_language:
        return text

    prompt = (
        f'Translate this quote to {fanpage.caption_language} for the Facebook page "{fanpage.name}".\n'
        f'QUOTE: {text}\n'
        f"- Preserve the exact meaning and the speaker's name/attribution — do not paraphrase or embellish.\n"
        f"- If it's already in {fanpage.caption_language}, return it unchanged.\n"
        f"- Keep the same `Speaker: \"the quote\"` format if the source has one.\n"
        f"Output ONLY the translated quote — no explanation."
    )
    translated, _ = generate_caption(prompt)
    return translated.strip().strip('"')


def _build_quote_attribution(speaker: str, secondary: str, topic: str = "") -> str:
    """Return a short attribution line for the quote card subtitle.

    Format: "SPEAKER_SURNAME ON TOPIC" (e.g. "ROSBERG ON LECLERC").
    Returns "" if both inputs are empty or unrecognisable.
    """
    if not speaker and not secondary and not topic:
        return ""
    # Take only the last word of the speaker name as the surname
    # ("Nico Rosberg" → "ROSBERG", "CARLOS SAINZ" → "SAINZ").
    surname = speaker.strip().split()[-1].upper() if speaker.strip() else ""
    # Topic: ≤3 words / ~28 chars; drop trailing preposition.
    _TRAILING_PREPS = {"ON", "IN", "AT", "OF", "FOR", "TO", "BY", "WITH", "THE", "A", "AN"}
    topic_str = ""
    raw_topic = topic if topic.strip() else secondary
    if raw_topic.strip():
        words = raw_topic.strip().upper().split()[:3]
        while words and words[-1] in _TRAILING_PREPS:
            words.pop()
        topic_str = " ".join(words)[:28].rstrip()
    if surname and topic_str:
        return f"{surname} ON {topic_str}"
    if surname:
        return surname
    return topic_str


@celery_app.task(name="app.tasks.ig_recreate.recreate_post_for_fanpage", bind=True, max_retries=3)
def recreate_post_for_fanpage(self, post_id: int, fanpage_id: int, radar_decision_id: int | None = None):
    db = SessionLocal()
    try:
        from app.models.posts import Post
        from app.models.target_fanpages import TargetFanpage
        from app.models.ig_sources import IGSource
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType, AIProvider
        from app.services.ai_caption import build_caption_prompt, generate_caption, GroqRateLimitError
        from app.services.ig_content_classifier import analyze_ig_post, is_f1_niche
        
        from app.models.radar import RadarStoryDecision, DecisionStatus
        from app.services.facebook_photo_source import is_incomplete_quote
        from app.services.radar_clustering import quote_matches_source, captions_too_similar

        decision = None
        if radar_decision_id:
            decision = db.query(RadarStoryDecision).filter_by(id=radar_decision_id).first()
            if not decision:
                return

        # Idempotency: one job per (post, fanpage)
        if db.query(PublishJob).filter_by(post_id=post_id, fanpage_id=fanpage_id).first():
            return

        post = db.query(Post).filter_by(id=post_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
        if not post or not fanpage or not fanpage.ig_recreate_enabled:
            return
        if not post.image_local_paths:
            logger.warning("IG-recreate: post %d has no images", post_id)
            return

        ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first()
        niche = (ig_source.ig_username if ig_source else None) or fanpage.name

        with open(post.image_local_paths[0], "rb") as f:
            image_bytes = f.read()
        try:
            niches = (fanpage.radar_niches or []) + (fanpage.mode2_gallery_niches or [])
            analysis = analyze_ig_post(
                image_bytes, 
                post.original_caption or "", 
                niche=niche, 
                allow_team_radio=is_f1_niche(niches)
            )
        except Exception as exc:
            logger.warning("IG-recreate: classify failed post %d fp %d: %s", post_id, fanpage_id, exc)
            raise self.retry(exc=exc, countdown=180)

        ctype = analysis.type
        
        if decision:
            if ctype == "other" or not analysis.text:
                if fanpage.visual_engine in ('chatgpt', 'auto', 'gflow'):
                    # We can proceed to create a job for redesign
                    pass
                else:
                    decision.status = DecisionStatus.AWAITING_VISUAL_ENGINE
                    db.commit()
                    return

            if ctype == "quote":
                extracted_quote = analysis.text
                if is_incomplete_quote(extracted_quote):
                    decision.status = DecisionStatus.HELD_QUOTE_MISMATCH
                    decision.reason = "incomplete quote"
                    db.commit()
                    return
                
                source_captions = [post.original_caption or ""]
                if decision.story:
                    for m in decision.story.posts:
                        if m.caption:
                            source_captions.append(m.caption)
                
                # Check quote_matches_source. But if all source_captions are empty, accept OCR text
                non_empty_sources = [c for c in source_captions if c.strip()]
                if non_empty_sources:
                    if not quote_matches_source(extracted_quote, non_empty_sources):
                        decision.status = DecisionStatus.HELD_QUOTE_MISMATCH
                        decision.reason = "quote text mismatch"
                        db.commit()
                        return

        if decision and ctype in ("other", "team_radio"):
            # It's an other or team_radio post but radar decided to proceed
            pass
        elif ctype == "other" or not analysis.text:
            logger.info("IG-recreate: post %d fp %d classified '%s' → skipped", post_id, fanpage_id, ctype)
            return

        from app.services.design_images import resolve_template

        other_jobs = []
        if decision and decision.story:
            other_jobs = db.query(PublishJob).join(RadarStoryDecision, RadarStoryDecision.publish_job_id == PublishJob.id).filter(
                RadarStoryDecision.story_id == decision.story_id,
                RadarStoryDecision.fanpage_id != fanpage_id
            ).all()

        if ctype in ("quote", "team_radio"):
            try:
                # D24: radio lines are taken verbatim; the redesign renders analysis.radio_lines, this title is only the template fallback.
                design_title = _translate_quote(analysis.text, fanpage)
            except GroqRateLimitError:
                raise self.retry(countdown=120)
        else:  # news, other
            other_titles = [j.design_title for j in other_jobs if j.design_title]
            try:
                text_to_rewrite = analysis.moment_summary if ctype == "other" else analysis.text
                design_title = _rewrite_news_title(text_to_rewrite, post.original_caption or "", fanpage, other_titles=other_titles)
            except GroqRateLimitError:
                raise self.retry(countdown=120)

        # map "other" to "news" and "team_radio" to "quote" so we can resolve a template
        template_ctype = "news" if ctype == "other" else ("quote" if ctype == "team_radio" else ctype)

        # ctype ("quote"/"news") doubles as the template category — cascades
        # to the fanpage's default_{category}_template_id, then a shared
        # default template tagged with that category (see resolve_template).
        template = resolve_template(db, template_ctype, fanpage=fanpage)
        if not template:
            logger.warning("IG-recreate: fanpage %d has no %s template available", fanpage_id, template_ctype)
            return
        template_id = template.id

        # FB caption (per-source caption criteria override the fanpage's Mode-1).
        # For quote/team_radio posts, feed the ALREADY-TRANSLATED design_title (what's
        # actually rendered on the image) as both the context and the
        # verbatim quote to reproduce — using the raw pre-translation analysis.text
        # here would let the caption drift from what the image shows.
        caption_context = design_title if ctype in ("quote", "team_radio") else (analysis.moment_summary if ctype == "other" else analysis.text)
        
        other_captions = [j.ai_generated_caption for j in other_jobs if j.ai_generated_caption]

        def _gen_caption():
            prompt = build_caption_prompt(
                fanpage, niche, caption_context, source=ig_source,
                quote_text=None if (decision and ctype in ("quote", "team_radio")) else (design_title if ctype in ("quote", "team_radio") else None),
                with_attribution=False if decision else True
            )
            if decision:
                if ctype in ("quote", "team_radio"):
                    prompt += "\n- IMPORTANT: DO NOT repeat the quote/radio verbatim in the caption. The text is already on the image."
                if other_captions:
                    prompt += "\n- IMPORTANT: Write something clearly different from these other captions for the same story:\n"
                    for c in other_captions:
                        prompt += f"  * {c[:200]}...\n"
            return generate_caption(prompt)

        try:
            caption, provider = _gen_caption()
            
            if decision:
                # check similarity
                for _ in range(2):
                    too_similar = False
                    for oc in other_captions:
                        if captions_too_similar(caption, oc):
                            too_similar = True
                            break
                    if too_similar:
                        caption, provider = _gen_caption()
                    else:
                        break
                
                # final check
                for oc in other_captions:
                    if captions_too_similar(caption, oc):
                        decision.status = DecisionStatus.SKIPPED_SIMILAR_CAPTION
                        decision.reason = "caption too similar after retries"
                        db.commit()
                        return
                        
        except GroqRateLimitError:
            raise self.retry(countdown=120)

        job = PublishJob(
            post_id=post_id,
            fanpage_id=fanpage_id,
            content_type=ContentType.ig_recreate,
            design_title=design_title,
            ai_generated_caption=caption,
            ai_provider_used=AIProvider(provider),
            design_template_id=template_id,
            status=PublishJobStatus.pending_design,
            is_breaking=True if (decision and decision.rule in ("fast", "burst")) else False,
            design_analysis_json=__import__("dataclasses").asdict(analysis),
        )
        # Radar quote/team_radio cards: set attribution subtitle from speaker + secondary
        # so the "SPEAKER ON TOPIC" line renders correctly on the quote template.
        if decision and ctype in ("quote", "team_radio"):
            attr = _build_quote_attribution(analysis.speaker, analysis.secondary, analysis.topic)
            if attr:
                job.design_subtitle = attr
        db.add(job)
        db.flush()
        
        if decision:
            decision.status = DecisionStatus.CREATED
            decision.publish_job_id = job.id
            decision.caption_text = caption

        db.commit()
        logger.info("IG-recreate: post %d fp %d → %s job %d", post_id, fanpage_id, ctype, job.id)
        if fanpage.visual_engine in ('chatgpt', 'auto', 'gflow'):
            render_ig_recreate.apply_async((job.id,), queue='visual')
        else:
            render_ig_recreate.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("IG-recreate error post %d fp %d: %s", post_id, fanpage_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=120)
    finally:
        db.close()


@celery_app.task(name="app.tasks.ig_recreate.run_preview_redesign", bind=True, max_retries=0, soft_time_limit=1260, time_limit=1320)
def run_preview_redesign(self, decision_id: int):
    db = SessionLocal()
    try:
        from app.models.radar import RadarStoryDecision
        from app.models.target_fanpages import TargetFanpage
        from app.models.posts import Post
        from app.services.redesign_pipeline import redesign_card

        decision = db.query(RadarStoryDecision).filter_by(id=decision_id).first()
        if not decision:
            return
        decision.preview_status = "running"
        db.commit()

        fanpage = db.query(TargetFanpage).filter_by(id=decision.fanpage_id).first()
        post = db.query(Post).filter_by(id=decision.post_id).first()
        if not post or not fanpage:
            decision.preview_status = "failed"
            decision.preview_error = "Missing post or fanpage"
            db.commit()
            return

        try:
            res = redesign_card(db, post=post, fanpage=fanpage, job=None, decision=decision)
        except Exception as e:
            from app.services.redesign_pipeline import RedesignResult
            logger.exception("redesign_card crashed in preview")
            res = RedesignResult(ok=False, layout='solo', image_path=None, image_url=None, final_text_rendered_by='template', notes=[], problems=[str(e)], content_type=None)

        if res.ok and res.image_url:
            decision.preview_image_path = res.image_url
            decision.preview_status = "done"
            decision.preview_error = None
        else:
            decision.preview_status = "failed"
            decision.preview_error = ("; ".join(res.problems) if res.problems else "Unknown error")[:300]
        db.commit()
    except SoftTimeLimitExceeded:
        try:
            decision = db.query(RadarStoryDecision).filter_by(id=decision_id).first()
            if decision:
                decision.preview_status = "failed"
                decision.preview_error = "time limit"
                db.commit()
        except:
            pass
    except Exception as e:
        logger.exception("run_preview_redesign outer crash")
        try:
            decision = db.query(RadarStoryDecision).filter_by(id=decision_id).first()
            if decision:
                decision.preview_status = "failed"
                decision.preview_error = str(e)[:300]
                db.commit()
        except:
            pass
    finally:
        db.close()

@celery_app.task(name="app.tasks.ig_recreate.render_ig_recreate", bind=True, max_retries=2, soft_time_limit=1260, time_limit=1320)
def render_ig_recreate(self, job_id: int):
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode
        from app.models.posts import Post
        from app.models.design_templates import DesignTemplate

        # Atomic claim: pending_design -> rendering (see design_renderer.render_design
        # for why — prevents the same job being rendered/published twice).
        claimed = (
            db.query(PublishJob)
            .filter(
                PublishJob.id == job_id,
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.ig_recreate,
            )
            .update({"status": PublishJobStatus.rendering}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return

        job = db.query(PublishJob).filter_by(id=job_id).first()

        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        post = db.query(Post).filter_by(id=job.post_id).first()
        template = (
            db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
            if job.design_template_id else None
        )
        if not fanpage or not post or not template or not template.template_json:
            job.status = PublishJobStatus.pending_design
            job.last_error = "missing fanpage/post/template for IG-recreate"
            db.commit()
            return
        if not post.image_local_paths:
            job.status = PublishJobStatus.pending_design
            job.last_error = "post has no image"
            db.commit()
            return

        from app.models.radar import RadarStoryDecision, DecisionStatus
        decision = db.query(RadarStoryDecision).filter_by(publish_job_id=job.id).first()
        if decision:
            decision.preview_status = "running"
            db.commit()

        from app.tasks.design_renderer import clean_title_for_render, clear_template_text_objects
        job.design_title = clean_title_for_render(job.design_title or "")
        if not job.design_title:
            job.status = PublishJobStatus.failed
            job.last_error = "empty design_title"
            if decision:
                decision.preview_status = "failed"
            db.commit()
            logger.warning("IG-recreate: job %d failed — empty design_title", job.id)
            return

        from app.services.ig_content_classifier import PostAnalysis
        pre_analysis = None
        if job.design_analysis_json:
            try:
                import dataclasses
                valid_fields = dataclasses.fields(PostAnalysis)
                valid_keys = {f.name for f in valid_fields}
                kwargs = {k: v for k, v in job.design_analysis_json.items() if k in valid_keys}
                for f in valid_fields:
                    if f.name not in kwargs and f.default == dataclasses.MISSING and f.default_factory == dataclasses.MISSING:
                        kwargs[f.name] = () if 'Tuple' in str(f.type) else '' if f.type == 'str' else None
                pre_analysis = PostAnalysis(**kwargs)
            except Exception:
                pass

        if fanpage.visual_engine in ('chatgpt', 'auto', 'gflow'):
            if fanpage.visual_engine == 'gflow':
                logger.info("Treating gflow as chatgpt for redesign_card")
                
            from app.services.redesign_pipeline import redesign_card
            try:
                res = redesign_card(db, post=post, fanpage=fanpage, job=job, decision=decision, pre_analysis=pre_analysis)
            except Exception as e:
                from app.services.redesign_pipeline import RedesignResult
                logger.exception("redesign_card crashed")
                res = RedesignResult(ok=False, layout='solo', image_path=None, image_url=None, final_text_rendered_by='template', notes=[], problems=[str(e)], content_type=None)

            if res.ok:
                job.design_image_path = res.image_path
                job.design_image_url = res.image_url
                job.status = PublishJobStatus.pending_publish
                if decision:
                    decision.preview_status = "done"
                    decision.preview_error = None
                db.commit()
                return
            else:
                short_err = "; ".join(res.problems)[:200]
                job.last_error = f"Redesign failed: {short_err}"
                
                # If "other", there is no safe fallback
                ctype_is_other = (res.content_type == 'other') or (res.content_type is None and decision and decision.story and decision.story.content_hint == 'other')
                if ctype_is_other:
                    if decision:
                        decision.status = DecisionStatus.FAILED
                        decision.reason = f"Redesign failed: {short_err}"
                        decision.preview_status = "failed"
                        decision.preview_error = short_err[:300]
                    job.status = PublishJobStatus.failed
                    db.commit()
                    return
                    
                if decision:
                    decision.preview_status = "failed"
                    decision.preview_error = short_err[:300]
                db.commit()
                # continue with existing path

        # Default photo = the IG post image (fallback only).
        main_path = post.image_local_paths[0]
        with open(main_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        image_src = f"data:image/jpeg;base64,{b64}"

        # Two-slot templates also get a secondary photo: circular inset → the
        # second subject's portrait placed opposite the main face; rect slot →
        # a two-subject split with style-consistent photos.
        from app.services.design_images import (
            prepare_design_images, extract_two_subjects, focus_points_for, source_news_main,
            watermark_datauri, _safe_face_cy_ceiling,
        )
        from app.models.ig_sources import IGSource
        ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first()
        niche = (ig_source.ig_username if ig_source else None) or fanpage.name

        # News recreate: use a clean, relevant photo (gallery → fresh search) by the
        # headline's subject instead of the IG screenshot. Falls back to the IG
        # image if nothing is found. Keyed off the resolved template's category
        # (not a specific template id) — it survives falling back to a shared
        # default template, not just the fanpage's own pinned news template.
        is_news_template = template.category == "news"
        
        if decision or is_news_template:
            # If it's a radar job, we always source a news main photo for both news and quote
            # because radar jobs must not use the IG source image (branding)
            exclude_paths = set()
            if decision and decision.story_id:
                from app.services.visual_sourcing import decision_photo_keys
                exclude_paths = decision_photo_keys(db, decision.story_id, job.fanpage_id)
            
            grounding = job.design_title or ""
            known_primary = None
            if pre_analysis:
                parts = []
                if pre_analysis.main_subject:
                    known_primary = pre_analysis.main_subject
                    parts.append(pre_analysis.main_subject)
                elif pre_analysis.speaker:
                    known_primary = pre_analysis.speaker
                    parts.append(pre_analysis.speaker)
                
                if parts and pre_analysis.inset_context:
                    parts.append(pre_analysis.inset_context)
                
                if parts:
                    grounding = " ".join(parts)
                
            src, path = source_news_main(db, grounding, niche, exclude_paths=exclude_paths if exclude_paths else None, known_primary=known_primary)
            if src:
                image_src, main_path = src, path
                if decision:
                    decision.used_photo_key = f"{decision.used_photo_key}|{path}" if decision.used_photo_key else path
                    db.commit()
            elif decision:
                # No photo found -> left in "needs manual image" (pending_design) state,
                # but instruction says "decision skipped_no_distinct_photo" and job left in pending_design.
                job.status = PublishJobStatus.pending_design
                job.last_error = "no distinct photo found"
                decision.status = DecisionStatus.SKIPPED_NO_DISTINCT_PHOTO
                decision.reason = "no distinct photo found"
                db.commit()
                return

        smart = bool(fanpage.ig_recreate_smart_layout)
        
        # Smart layout: a news headline about TWO people → use the split template
        if smart and fanpage.ig_recreate_split_template_id and is_news_template:
            primary, secondary = extract_two_subjects(job.design_title, niche)
            if primary and secondary:
                split_tpl = db.query(DesignTemplate).filter_by(id=fanpage.ig_recreate_split_template_id).first()
                if split_tpl and split_tpl.template_json:
                    template = split_tpl
                    logger.info("IG-recreate: smart split for job %d (%s | %s)", job.id, primary, secondary)

        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            job.design_title, niche, image_src, main_path=main_path, smart=smart,
            expand=bool(fanpage.design_expand),
        )

        template_json = clear_template_text_objects(template_json)
        # Fanpage templates render exactly as configured — no colour/icon override.
        # visual_style (accent + treatment) is used only for the AI image prompt
        # in redesign_pipeline.redesign_card, not the template layer.

        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": job.design_title,
                "subtitle": job.design_subtitle or "",
                "caption": job.design_caption or "",
                # No fallback to fanpage name/username — a fanpage that hasn't
                # set an explicit watermark_text/watermark_image gets NO
                # watermark on the design at all.
                "watermark": fanpage.watermark_text or "",
                "watermark_image": watermark_datauri(fanpage),
                "image_srcs": image_srcs,
                "focus_points": focus_points_for(
                    image_srcs, safe_cy_ceiling=_safe_face_cy_ceiling(template_json, template.canvas_height)
                ),
                "scale": settings.design_render_scale,
            },
            timeout=_RENDER_TIMEOUT,
        )
        resp.raise_for_status()
        png = resp.content

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        db.commit()
        logger.info("IG-recreate: job %d rendered → %s (%d bytes)", job_id, filename, len(png))

        if fanpage.publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except SoftTimeLimitExceeded:
        db.rollback()
        try:
            from app.models.publish_jobs import PublishJob, PublishJobStatus
            job = db.query(PublishJob).filter_by(id=job_id).first()
            if job and job.status == PublishJobStatus.rendering:
                job.status = PublishJobStatus.pending_design
                job.last_error = "time limit"
                db.commit()
        except Exception:
            pass
    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("IG-recreate render job %d failed: %s", job_id, exc, exc_info=True)
        try:
            from app.models.publish_jobs import PublishJob, PublishJobStatus
            job = db.query(PublishJob).filter_by(id=job_id).first()
            if job and job.status == PublishJobStatus.rendering:
                job.status = PublishJobStatus.pending_design
                db.commit()
        except Exception:
            db.rollback()
        raise self.retry(exc=exc, countdown=180)
    finally:
        db.close()
