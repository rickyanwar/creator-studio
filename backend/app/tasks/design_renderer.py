"""Design renderer tasks — turn pending_design news jobs into rendered PNGs.

Auto mode (spec Fitur 2 §D): pick an image (workflow §C cascade), send the
fanpage's Fabric.js template + AI title + image to the renderer service
(Puppeteer + Fabric.js, concurrency 1), store the PNG under
/var/www/media/designs/, and move the job to pending_publish.

Review-mode jobs are NOT auto-rendered — the admin opens them in the designer
UI, edits freely, and exports (the export endpoint stores the PNG the same
way). The beat sweep therefore only dispatches jobs whose fanpage is in auto
mode; render_design itself can also be triggered manually from the UI.
"""

import base64
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

import httpx

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings
from app.services.brand_look import brand_look

logger = logging.getLogger(__name__)
settings = get_settings()

import copy


def _redis():
    import redis
    return redis.from_url(get_settings().redis_url, decode_responses=True)


_RENDER_QUEUE_LOCK_TTL = 1800
NEWS_STALE_HOURS = 24


def _acquire_queued_lock(job_id: int) -> bool:
    """Take a per-job Redis lock before enqueueing render tasks (news, discussion, fb photo, ig recreate).
    SET render_design:queued:<job_id> 1 NX EX 1800.
    Returns True if lock acquired or Redis is unreachable (fall back to enqueue).
    Returns False if key already exists (skip this sweep).
    """
    key = f"render_design:queued:{job_id}"
    try:
        r = _redis()
        acquired = r.set(key, "1", nx=True, ex=_RENDER_QUEUE_LOCK_TTL)
        return bool(acquired)
    except Exception as exc:
        logger.warning("Redis unreachable while acquiring queue lock for job %s: %s", job_id, exc)
        return True


def _release_queued_lock(job_id: int | None) -> None:
    """Delete the per-job Redis queue lock when render finishes."""
    if not job_id:
        return
    key = f"render_design:queued:{job_id}"
    try:
        r = _redis()
        r.delete(key)
    except Exception as exc:
        logger.warning("Redis unreachable while releasing queue lock for job %s: %s", job_id, exc)


def clean_title_for_render(title: str) -> str:
    if not title:
        return ""
    return title.strip()

def clear_template_text_objects(template_json: dict) -> dict:
    if not template_json:
        return template_json
    tj = copy.deepcopy(template_json)
    for obj in tj.get("objects", []):
        role = obj.get("placeholderRole")
        if role in ("title", "subtitle", "caption", "label"):
            if "text" in obj:
                obj["text"] = ""
    return tj


def _speaker_line_fields(template_json, subtitle: str, caption: str) -> tuple[str, str]:
    """Templates without a subtitle placeholder (GP Quote) show the speaker
    name in their caption line. Returns (subtitle, caption) for the payload."""
    from app.services.design_images import find_role_object
    if (not caption) and subtitle and find_role_object(template_json, "subtitle") is None \
            and find_role_object(template_json, "caption") is not None:
        return "", subtitle
    return subtitle, caption

_RENDER_TIMEOUT = 120.0
_HEADLINE_NAME_RE = re.compile(
    r"^\s*([A-Z][A-Za-zÀ-ſ.'’-]+(?:\s+[A-Z][A-Za-zÀ-ſ.'’-]+){0,3})\s*:\s+\S"
)


def _name_badge(value: str | None) -> str | None:
    value = (value or "").replace("**", "").strip()
    if not value or len(value) > 40 or any(char in value for char in ".?,;!"):
        return None
    words = value.split()
    if not 1 <= len(words) <= 4 or any(not word[0].isupper() for word in words):
        return None
    return value


def _is_likely_speaker_person(db, name: str) -> bool:
    """Return True if *name* from a headline prefix looks like a real person:
    - 2 or more capitalized words (e.g. 'Max Verstappen', 'Jorge Martin'), OR
    - A single word that equals a known driver surname or full name in f1_drivers, OR
    - A single word that equals a confirmed person in GalleryImage (where keyword matches
      and label is 'face' or shot_type is portrait/face/half_body, or where attributes['people']
      records the person).
    Never treats circuits, venues, teams, or locations (e.g. 'Portimao') as a person."""
    if not name:
        return False
    words = name.strip().split()
    if not words:
        return False
    if len(words) >= 2 and all(w[0].isupper() for w in words):
        return True
    if len(words) == 1 and db is not None:
        single = words[0].strip()
        try:
            from app.models.f1_drivers import F1Driver
            from sqlalchemy import func, or_
            driver = db.query(F1Driver.id).filter(
                or_(
                    func.lower(F1Driver.surname) == single.lower(),
                    func.lower(F1Driver.full_name) == single.lower(),
                )
            ).first()
            if driver is not None:
                return True
        except Exception:
            pass

        try:
            from app.models.gallery import GalleryImage
            from sqlalchemy import func, or_, and_, cast, String
            pattern = f"%{single}%"
            row = db.query(GalleryImage.id).filter(
                GalleryImage.is_deleted.is_(False),
                or_(
                    and_(
                        func.lower(GalleryImage.keyword) == single.lower(),
                        or_(
                            GalleryImage.label == "face",
                            cast(GalleryImage.attributes["shot_type"], String).in_(["face_closeup", "portrait", "half_body"]),
                        ),
                    ),
                    cast(GalleryImage.attributes["people"], String).ilike(pattern),
                ),
            ).first()
            if row is not None:
                return True
        except Exception:
            pass
    return False


def _maybe_compose_speaker_design(db, fanpage, job, article, *, mode: str) -> bytes | None:
    """Return a speaker-led image when the per-mode toggle is enabled."""
    toggle = getattr(fanpage, "news_visual_engine" if mode == "news" else "fb_visual_engine", "off")
    if toggle != "chatgpt":
        return None

    from app.services.design_images import extract_secondary_subject, extract_two_subjects
    from app.services.speaker_design import compose_speaker_design, pick_speaker_photos

    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
    article_title = getattr(article, "scraped_title", "") or ""
    title = job.design_title or article_title
    speaker = _name_badge(job.design_subtitle)
    if not speaker:
        match = _HEADLINE_NAME_RE.match(article_title) or _HEADLINE_NAME_RE.match(title)
        candidate = match.group(1).strip() if match else ""
        if candidate and _is_likely_speaker_person(db, candidate):
            speaker = candidate

    extraction_title = " — ".join(
        part for part in (job.design_subtitle, getattr(article, "scraped_title", None), title) if part
    ) or title
    primary, secondary = extract_two_subjects(extraction_title, niche)
    if not speaker:
        speaker = (primary or "").strip()
    subjects = [name for name in (primary, secondary) if name and name.strip() and name.strip().lower() != speaker.lower()]
    if not subjects and speaker:
        secondary_name = extract_secondary_subject(extraction_title, niche)
        if secondary_name and secondary_name.strip().lower() != speaker.lower():
            subjects = [secondary_name.strip()]
    if not speaker:
        return None
    if not pick_speaker_photos(db, speaker):
        return None

    article_text = "\n".join(
        part for part in (
            getattr(article, "scraped_title", None),
            getattr(article, "scraped_content", None),
        ) if part
    )[:2000]
    around = (
        getattr(article, "article_published_at", None)
        or getattr(article, "scraped_at", None)
        or getattr(job, "created_at", None)
        or datetime.now(timezone.utc).replace(tzinfo=None)
    )
    niche_lower = niche.lower()
    sport = "MotoGP" if any(term in niche_lower for term in ("motogp", "motorsport", "motorcycle", "bike")) else niche

    story_photo_path: str | None = None
    story_photo_desc: str | None = None
    if article is not None:
        try:
            import io
            from PIL import Image
            from app.config import get_settings
            from app.services.story_photo import describe_story_photo, fetch_story_photo

            photo_bytes = fetch_story_photo(article)
            if photo_bytes:
                fmt = (Image.open(io.BytesIO(photo_bytes)).format or "JPEG").upper()
                ext = ".png" if fmt == "PNG" else (".webp" if fmt == "WEBP" else ".jpg")
                designs_dir = Path(get_settings().storage_base_path) / "designs"
                designs_dir.mkdir(parents=True, exist_ok=True)
                job_id = getattr(job, "id", None) or "tmp"
                p = designs_dir / f"story_photo_{job_id}_{uuid.uuid4().hex[:8]}{ext}"
                p.write_bytes(photo_bytes)
                story_photo_path = str(p)
                story_photo_desc = describe_story_photo(photo_bytes, article_title)
        except Exception as exc:
            logger.info("Story photo inset preparation failed: %s", exc)
            story_photo_path = None
            story_photo_desc = None

    try:
        res = compose_speaker_design(
            db,
            fanpage,
            speaker=speaker,
            subjects=subjects,
            article_text=article_text,
            sport=sport,
            around=around,
            story_photo_path=story_photo_path,
            story_photo_desc=story_photo_desc,
            content_kind=mode,
        )
        return res
    finally:
        if story_photo_path:
            try:
                Path(story_photo_path).unlink(missing_ok=True)
            except Exception:
                pass


def select_image_for_job(db, job, fanpage, article, exclude_marker: str | None = None) -> tuple[str | None, object | None, str | None]:
    """Workflow §C cascade. Returns (image_src_data_uri_or_none, gallery_image_or_none, marker).

    1. the article's actual subject (AI-extracted from the title) → a photo of
       THAT person specifically: gallery vision-match first, fresh Getty search
       if the gallery has nothing — same subject-sourcing Mode 3 IG-recreate
       uses (design_images.source_news_main). This is what keeps the photo on
       the person the headline is actually about instead of a random face from
       the niche pool.
    2. niche keywords that literally appear in the article title/content →
       unused gallery image (covers headlines with no single clear subject)
    3. no gallery match at all → fresh topic search (Getty, then Google Images)
       on the headline itself, kept only if 9Router vision confirms the result
       actually matches this story (design_images.fetch_topic_datauri).
    4. None → job needs a manual image

    The article's own scraped_image_url (its og:image / hero photo) is
    deliberately NOT used here (removed 2026-08-17) — it's the original
    publisher's/photographer's photo, not licensed editorial stock like
    Getty, so auto-publishing it is a real copyright exposure. It's still
    offered as one labelled, human-reviewed option in the Designer's manual
    candidate list (api/publish_jobs.get_design_payload) — a person
    consciously choosing it is a different risk than the pipeline silently
    picking it for them.

    `exclude_marker` (from job.last_image_marker) is whatever this function
    returned as the marker last time it ran for this job — the History
    "Re-edit with new image" action resets the job to pending_design without
    clearing it, specifically so this retry skips landing on the exact same
    photo again: tier 1 excludes it via source_news_main's exclude_path (its
    gallery lookup is NOT a fresh search — it hits the same downloaded photos
    first, so without this a subject with only one or two gallery images
    would just get the same one back forever), and the matching GalleryImage
    row is skipped in tier 2. Tier 3 is a genuinely fresh AI search each call
    already, so no exclusion is needed there.
    """
    from sqlalchemy import or_
    from app.models.gallery import GalleryImage
    from app.services.gallery_filters import not_keyword_mismatch
    from app.services.design_images import (
        niche_keywords, source_news_main, fetch_topic_datauri,
        _eligible_rows, _mark_gallery_image_used,
    )

    excluded_gallery_id = (
        int(exclude_marker.split(":", 1)[1])
        if exclude_marker and exclude_marker.startswith("gallery:")
        else None
    )
    excluded_local_path = (
        db.query(GalleryImage.local_path).filter_by(id=excluded_gallery_id).scalar()
        if excluded_gallery_id is not None
        else None
    )

    niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
    # Names often land in the subtitle, not the headline (e.g. "X could leave
    # the team" / "Y reportedly picked over Z for the seat") — give the
    # subject-extraction both lines, not just the title.
    heading = job.design_title or article.scraped_title
    subtitle = job.design_subtitle or ""
    title = f"{heading}. {subtitle}".strip(". ")
    try:
        src, path = source_news_main(db, title, niche, exclude_paths={excluded_local_path} if excluded_local_path else None)
        if src:
            gi = db.query(GalleryImage).filter_by(local_path=path).first() if path else None
            return src, gi, (f"gallery:{gi.id}" if gi else "search")
    except Exception as exc:
        logger.warning("Design: subject-photo lookup failed for job %d: %s", job.id, exc)

    keywords = niche_keywords(db, fanpage.mode2_gallery_niches or [])
    text = f"{article.scraped_title} {article.scraped_content or ''}".lower()
    matched = [k for k in keywords if k in text]

    def _pick_from_pool(pool):
        for img in pool:
            if excluded_gallery_id is not None and img.id == excluded_gallery_id:
                continue
            if not img.local_path or not os.path.exists(img.local_path):
                continue
            try:
                data = Path(img.local_path).read_bytes()
                _mark_gallery_image_used(db, img)
                return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}", img, f"gallery:{img.id}"
            except OSError as exc:
                logger.warning("Design: gallery image %d unreadable (%s) — skipping", img.id, exc)
        return None

    matched_query = None
    if matched:
        matched_query = db.query(GalleryImage).filter(
            # A photo can feature more than one person — also match images
            # tagged with a pool keyword as a secondary (extra) tag.
            or_(GalleryImage.keyword.in_(matched), GalleryImage.extra_keywords.overlap(matched)),
            GalleryImage.is_deleted == False,
            not_keyword_mismatch(),
        )
        # Cooldown-fresh only here — a stale match isn't allowed to win over
        # the fresh Getty/Google search below just because it exists.
        picked = _pick_from_pool(_eligible_rows(matched_query, allow_stale_reuse=False))
        if picked:
            return picked

    # No cooldown-fresh gallery image (subject-specific AND niche-keyword both
    # came up empty/stale) — search fresh instead of jumping straight to a
    # stale reuse. Deliberately does NOT fall back to the article's own
    # scraped_image_url — see the docstring's copyright note.
    excerpt = (article.scraped_content or "")[:600]
    try:
        uri = fetch_topic_datauri(title, niche, excerpt=excerpt)
        if uri:
            return uri, None, "search"
    except Exception as exc:
        logger.warning("Design: topic-photo search failed for job %d: %s", job.id, exc)

    # Absolute last resort: nothing fresh anywhere — reuse a stale niche-pool
    # photo rather than stall the job entirely.
    if matched_query is not None:
        picked = _pick_from_pool(_eligible_rows(matched_query))
        if picked:
            logger.info("Design: job %d falling back to a stale (cooldown-expired) gallery photo — nothing fresher available", job.id)
            return picked

    return None, None, None


def render_card_png(
    job: Any,
    fanpage: Any,
    template: Any,
    image_src: str,
    *,
    label: str | None = None,
    article: Any = None,
    db: Any = None,
    main_path: str | None = None,
    skip_face_fix: bool = False,
) -> bytes:
    """Render a card PNG through the headless renderer service.

    Extracts the payload construction and POST to renderer:3001 so both
    render_design and pro_poster_eval produce identical card layouts.
    """
    from app.services.design_images import (
        prepare_design_images,
        focus_points_for,
        align_split_focus_points,
        watermark_datauri,
        find_role_object,
        _safe_face_cy_ceiling,
        fix_unsafe_single_photo_face,
    )

    art = article or getattr(job, "source_article", None)
    scraped_title = getattr(art, "scraped_title", "") if art else ""
    title = getattr(job, "design_title", None) or scraped_title or ""
    subtitle = getattr(job, "design_subtitle", None) or ""
    caption = getattr(job, "design_caption", None) or ""

    subject_title = " — ".join(
        p for p in (subtitle, scraped_title, title) if p
    ) or title

    session = db or (Session.object_session(job) if hasattr(job, "_sa_instance_state") else None)

    template_json, image_srcs = prepare_design_images(
        session,
        template.template_json,
        template.canvas_width,
        subject_title,
        getattr(fanpage, "name", "") if fanpage else "",
        image_src,
        main_path=main_path,
        expand=bool(getattr(fanpage, "design_expand", False)),
    )
    image_zooms = template_json.pop("_splitImageZooms", None)

    image_2_slot = find_role_object(template_json, "image_2")
    is_split = (
        image_2_slot is not None
        and image_2_slot.get("type") == "rect"
        and len(image_srcs) >= 2
        and image_srcs[0]
        and image_srcs[1]
    )
    safe_cy_ceiling = _safe_face_cy_ceiling(template_json, template.canvas_height)
    if not is_split and not skip_face_fix and image_srcs and image_srcs[0]:
        try:
            main_bytes = base64.b64decode(image_srcs[0].split(",", 1)[1])
            fixed = fix_unsafe_single_photo_face(
                main_bytes, template.canvas_width, template.canvas_height, safe_cy_ceiling,
            )
            if fixed:
                image_srcs[0] = fixed
        except Exception as exc:
            logger.warning("Design: fix_unsafe_single_photo_face failed for job %s: %s", getattr(job, "id", None), exc)

    focus_points = focus_points_for(image_srcs, for_split=False, safe_cy_ceiling=safe_cy_ceiling)
    if is_split:
        focus_points = align_split_focus_points(focus_points)

    if label is not None:
        news_badge_text = label
    else:
        category = getattr(template, "category", "news") or "news"
        breaking = getattr(job, "is_breaking", False)
        news_badge_text = _news_badge_text(fanpage, breaking=breaking) if category == "news" else ""

    title = clean_title_for_render(title)
    template_json = clear_template_text_objects(template_json)
    sub_text, cap_text = _speaker_line_fields(template_json, subtitle, caption)

    cfg = get_settings()
    renderer_url = cfg.renderer_url.rstrip("/")
    resp = httpx.post(
        f"{renderer_url}/render",
        json={
            "template_json": template_json,
            "width": template.canvas_width,
            "height": template.canvas_height,
            "title": title,
            "subtitle": sub_text,
            "caption": cap_text,
            "label": news_badge_text,
            "watermark": (getattr(fanpage, "watermark_text", None) or "") if fanpage else "",
            "watermark_image": watermark_datauri(fanpage) if fanpage else None,
            "image_srcs": image_srcs,
            "focus_points": focus_points,
            "image_zooms": image_zooms,
            "scale": cfg.design_render_scale,
            "accent": getattr(fanpage, "accent_color", None) if fanpage else None,
            "look": brand_look(fanpage, template_json) if fanpage else None,
        },
        timeout=_RENDER_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.content


@celery_app.task(name="app.tasks.design_renderer.render_design", bind=True, max_retries=2)
def render_design(self, job_id: int):
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage
        from app.models.scraped_articles import ScrapedArticle, ArticleStatus

        # Atomic claim: pending_design -> rendering. If another runner (a
        # slow prior attempt still in flight when the beat sweep re-dispatched
        # it, a retry, a manual render-now click) already claimed this job,
        # this UPDATE matches zero rows and we exit — preventing two renders
        # (and two downstream publishes) for the same job.
        claimed = (
            db.query(PublishJob)
            .filter(
                PublishJob.id == job_id,
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.news_content,
            )
            .update({"status": PublishJobStatus.rendering}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return

        job = db.query(PublishJob).filter_by(id=job_id).first()
        job.design_not_before = None

        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        article = db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()
        if not fanpage or not article:
            job.status = PublishJobStatus.pending_design
            job.last_error = "fanpage or article missing"
            db.commit()
            return

        # ── Resolve template: job override → fanpage's default_{category}_template_id
        # → shared category default (see design_images.resolve_template).
        # The copywriter classifies each article as "news" or "quote" and pins
        # job.design_template_id to the matching pool; that template's own
        # category is the source of truth here (survives falling back to a
        # shared default, not just the fanpage's own pinned template) — same
        # pattern as ig_recreate's is_news_template check. ──
        from app.models.design_templates import DesignTemplate
        from app.services.design_images import resolve_template

        job_template = (
            db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
            if job.design_template_id else None
        )
        category = job_template.category if job_template and job_template.category else "news"
        template = resolve_template(db, category, fanpage=fanpage, job_template_id=job.design_template_id)
        if not template or not template.template_json:
            job.status = PublishJobStatus.pending_design
            job.last_error = "no design template configured — create one in Template Designer"
            db.commit()
            logger.warning("Design: job %d has no usable template", job_id)
            return

        # ── Image selection (workflow §C) ──
        from app.services.design_images import (
            prepare_design_images, focus_points_for, align_split_focus_points,
            watermark_datauri, find_role_object, _safe_face_cy_ceiling,
            fix_unsafe_single_photo_face, single_photo_face_fits,
        )

        safe_cy_ceiling = _safe_face_cy_ceiling(template.template_json, template.canvas_height)

        # A candidate whose face is already too large to ever clear the
        # text zone (see single_photo_face_fits — real incident, 2026-08-27:
        # quote cards shipped with the mouth/chin cropped off by the caption)
        # is rejected outright and the NEXT candidate tried, same
        # exclude_marker mechanism the manual "re-edit with new image" action
        # already uses — capped so a niche with only bad photos still lands
        # on manual review instead of looping forever.
        image_src = gallery_image = image_marker = None
        speaker_gallery_images = []
        pro_poster_label = None
        pro_poster_engine = None
        pro_poster_engine_note = None
        pro_poster_spent = False

        from app.services.visual_engine import VisualEngineDeferred
        from app.services.pro_poster.pipeline import compose_pro_poster, pro_poster_applies

        if pro_poster_applies(db, fanpage):
            try:
                pp_res = compose_pro_poster(db, fanpage, job, article)
                pro_poster_spent = bool(pp_res and pp_res.attempts >= 1 and not pp_res.image)
                analysis = dict(job.design_analysis_json or {})
                if pp_res.qa is not None:
                    analysis["pro_poster_qa"] = {
                        "scores": pp_res.qa.scores,
                        "issues": list(pp_res.qa.issues),
                        "passed": pp_res.qa.passed,
                    }
                else:
                    analysis["pro_poster_qa"] = None
                job.design_analysis_json = analysis

                if pp_res.image:
                    image_src = f"data:image/png;base64,{base64.b64encode(pp_res.image).decode()}"
                    image_marker = "pro_poster"
                    pro_poster_engine = pp_res.engine
                    scores_str = " ".join(f"{k}:{v}" for k, v in pp_res.qa.scores.items()) if (pp_res.qa and pp_res.qa.scores) else ""
                    note_text = f"pro poster · qa {scores_str}".strip()
                    pro_poster_engine_note = note_text[:200]
                    pro_poster_label = pp_res.brief.badge_label
                elif pp_res.fallback_reason == "no verified photo":
                    # TYPOGRAPHIC CARD
                    from app.services.pro_poster import typo_background
                    bg_bytes = typo_background.make_typographic_background(
                        pp_res.brief,
                        brand_color=getattr(fanpage, "accent_color", None),
                        seed=job.id,
                    )
                    image_src = typo_background.background_datauri(bg_bytes)
                    image_marker = "pro_poster_typo"
                    pro_poster_engine = "template"
                    pro_poster_engine_note = (pp_res.fallback_reason or "")[:200]
                    pro_poster_label = pp_res.brief.badge_label
                else:
                    logger.info("Pro poster job %d fell back to the old path: %s", job_id, pp_res.fallback_reason)
            except VisualEngineDeferred as exc:
                _defer_job(db, job, exc)
                return
            except Exception as exc:
                logger.exception("Pro poster compose failed for job %d, continuing: %s", job_id, exc)

        if not image_src:
            if pro_poster_spent:
                logger.info("Pro poster job %d already used its AI generation; fallback uses the template card", job_id)
                speaker_design = None
            else:
                try:
                    speaker_design = _maybe_compose_speaker_design(db, fanpage, job, article, mode="news")
                except VisualEngineDeferred as exc:
                    _defer_job(db, job, exc)
                    return
            if speaker_design:
                speaker_gallery_images = getattr(speaker_design, "used_gallery_images", [])
                image_src = f"data:image/png;base64,{base64.b64encode(speaker_design).decode()}"
                image_marker = "speaker_design"
            else:
                exclude_marker = job.last_image_marker
                for _attempt in range(3):
                    cand_src, cand_gi, cand_marker = select_image_for_job(
                        db, job, fanpage, article, exclude_marker=exclude_marker,
                    )
                    if not cand_src:
                        break
                    try:
                        cand_bytes = base64.b64decode(cand_src.split(",", 1)[1])
                        fits = single_photo_face_fits(
                            cand_bytes, template.canvas_width, template.canvas_height, safe_cy_ceiling,
                        )
                    except Exception:
                        fits = True  # can't decode to check — don't block over a hiccup
                    if fits:
                        image_src, gallery_image, image_marker = cand_src, cand_gi, cand_marker
                        break
                    logger.info(
                        "Design: job %d rejected candidate %s — face too large for this template's safe zone",
                        job_id, cand_marker,
                    )
                    exclude_marker = cand_marker
        if not image_src:
            job.status = PublishJobStatus.pending_design
            job.last_error = "needs manual image — no gallery match and no article hero image"
            db.commit()
            logger.warning("Design: job %d needs a manual image", job_id)
            return

        skip_face_fix = (image_marker == "pro_poster_typo")
        png_bytes = render_card_png(
            job,
            fanpage,
            template,
            image_src,
            label=pro_poster_label,
            article=article,
            db=db,
            main_path=gallery_image.local_path if gallery_image else None,
            skip_face_fix=skip_face_fix,
        )

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png_bytes)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.design_template_id = template.id
        job.last_image_marker = image_marker
        if image_marker in ("pro_poster", "pro_poster_typo"):
            job.design_engine = pro_poster_engine
            job.design_engine_note = pro_poster_engine_note
        elif image_marker == "speaker_design":
            job.design_engine = getattr(speaker_design, "engine", None) or "codex"
            note = getattr(speaker_design, "engine_note", "") or None
            job.design_engine_note = note[:200] if note else None
        else:
            job.design_engine = "template"
            job.design_engine_note = (
                "AI image failed → template"
                if getattr(fanpage, "news_visual_engine", None) == "chatgpt"
                else None
            )
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        if gallery_image:
            gallery_image.is_used = True
            gallery_image.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        for s_gi in speaker_gallery_images:
            s_gi.is_used = True
            s_gi.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        article.status = ArticleStatus.designed
        db.commit()

        logger.info("Design: job %d rendered → %s (%d bytes)", job_id, filename, len(png_bytes))

        # Auto-mode fanpages go straight to Repliz (Phase 2E); review-mode jobs
        # wait for the admin to publish from the queue/designer.
        from app.models.target_fanpages import PublishMode
        if fanpage.mode2_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Design: job %d render failed: %s", job_id, exc, exc_info=True)
        try:
            from app.models.publish_jobs import PublishJob, PublishJobStatus
            job = db.query(PublishJob).filter_by(id=job_id).first()
            # Release the claim so the retry (or, once retries are exhausted,
            # the next beat sweep) can pick this job up again — otherwise it
            # would be stranded in 'rendering' forever, invisible to both.
            if job and job.status == PublishJobStatus.rendering:
                job.status = PublishJobStatus.pending_design
                db.commit()
        except Exception:
            db.rollback()
        raise self.retry(exc=exc, countdown=180)
    finally:
        _release_queued_lock(job_id)
        db.close()


@celery_app.task(name="app.tasks.design_renderer.render_discussion", bind=True, max_retries=2)
def render_discussion(self, job_id: int):
    """Render a Mode 4 discussion card: a full-canvas subject photo + the
    "DISCUSSION:/HOT TAKE:" label badge + the big debate line (job.design_title).

    Fields carried on the job by the discussion scheduler (see
    app/tasks/discussion.py): design_title=question, design_subtitle=label,
    design_caption=subject name (used here only to source the photo — the
    discussion template has no subtitle/caption slot, so neither renders as
    body text). Image sourcing is gallery-first (by subject) then a fresh Getty
    search — the same cascade Mode 2/3 use, minus the article/topic tiers a
    discussion card doesn't have.
    """
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode

        claimed = (
            db.query(PublishJob)
            .filter(
                PublishJob.id == job_id,
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.discussion,
            )
            .update({"status": PublishJobStatus.rendering}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return

        job = db.query(PublishJob).filter_by(id=job_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        if not fanpage:
            job.status = PublishJobStatus.pending_design
            job.last_error = "fanpage missing"
            db.commit()
            return

        from app.services.design_images import (
            resolve_template, find_gallery_datauri, fetch_subject_datauri,
            prepare_design_images, focus_points_for, watermark_datauri,
            align_split_focus_points, find_role_object, _safe_face_cy_ceiling,
            fix_unsafe_single_photo_face,
        )

        template = resolve_template(db, "discussion", fanpage=fanpage, job_template_id=job.design_template_id)
        if not template or not template.template_json:
            # Graceful fallback: no discussion template set/seeded → use the
            # fanpage's News template (already-existing or its configured
            # default). The label badge won't render on a news layout, but the
            # card still goes out (photo + big question) instead of stalling.
            template = resolve_template(db, "news", fanpage=fanpage)
            if template and template.template_json:
                logger.info("Discussion: job %d — no discussion template, falling back to News template %d", job_id, template.id)
        if not template or not template.template_json:
            job.status = PublishJobStatus.pending_design
            job.last_error = "no discussion or news template configured — create one in Template Designer"
            db.commit()
            logger.warning("Discussion: job %d has no usable template", job_id)
            return

        # ── Image: fresh-gallery first (cooldown-respecting), fresh Getty
        # fallback, stale-gallery reuse only as the true last resort ──
        subject = (job.design_caption or "").strip()
        niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
        image_src, gallery_image, image_marker = None, None, None
        if subject:
            try:
                uri, gi = find_gallery_datauri(db, subject, use_vision=True, image_type="face", allow_stale_reuse=False, niche=niche)
                if uri:
                    image_src, gallery_image, image_marker = uri, gi, (f"gallery:{gi.id}" if gi else "gallery")
            except Exception as exc:
                logger.warning("Discussion: gallery lookup failed for %r (job %d): %s", subject, job_id, exc)
            if not image_src:
                try:
                    uri, _ = fetch_subject_datauri(db, subject, "face", niche)
                    if uri:
                        image_src, image_marker = uri, "search"
                except Exception as exc:
                    logger.warning("Discussion: Getty fetch failed for %r (job %d): %s", subject, job_id, exc)
            if not image_src:
                # Nothing fresh anywhere — reuse a stale gallery photo rather
                # than stall the card entirely.
                try:
                    uri, gi = find_gallery_datauri(db, subject, use_vision=True, image_type="face", niche=niche)
                    if uri:
                        image_src, gallery_image, image_marker = uri, gi, (f"gallery:{gi.id}" if gi else "gallery")
                except Exception as exc:
                    logger.warning("Discussion: stale-gallery fallback failed for %r (job %d): %s", subject, job_id, exc)

        if not image_src:
            job.status = PublishJobStatus.pending_design
            job.last_error = f"needs manual image — no photo found for subject {subject!r}"
            db.commit()
            logger.warning("Discussion: job %d needs a manual image (subject=%r)", job_id, subject)
            return

        # smart=True: a discussion card whose template has a rect image_2 slot
        # (see extract_two_subjects/build_split_srcs) can now split into two
        # photos when job.design_title itself reads as a genuine head-to-head
        # ("X vs Y", "who's better") — hot-take questions are framed as
        # debates far more often than regular news headlines, so this tier
        # fires often. Falls back to the single `image_src` already sourced
        # above (main_datauri) whenever the title isn't duel-shaped or split
        # sourcing comes up empty — unchanged single-photo behavior either way.
        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            job.design_title or "", niche, image_src,
            main_path=gallery_image.local_path if gallery_image else None,
            smart=True, expand=bool(fanpage.design_expand),
        )
        image_zooms = template_json.pop("_splitImageZooms", None)

        image_2_slot = find_role_object(template_json, "image_2")
        is_split = (
            image_2_slot is not None and image_2_slot.get("type") == "rect"
            and len(image_srcs) >= 2 and image_srcs[0] and image_srcs[1]
        )
        safe_cy_ceiling = _safe_face_cy_ceiling(template_json, template.canvas_height)
        # See render_design's identical block for why this is needed
        # (zero cover-fit vertical slack on a landscape photo — the ceiling
        # alone can't be honored without it).
        if not is_split and image_srcs and image_srcs[0]:
            try:
                main_bytes = base64.b64decode(image_srcs[0].split(",", 1)[1])
                fixed = fix_unsafe_single_photo_face(
                    main_bytes, template.canvas_width, template.canvas_height, safe_cy_ceiling,
                )
                if fixed:
                    image_srcs[0] = fixed
            except Exception as exc:
                logger.warning("Discussion: fix_unsafe_single_photo_face failed for job %d: %s", job_id, exc)
        focus_points = focus_points_for(image_srcs, for_split=False, safe_cy_ceiling=safe_cy_ceiling)
        if is_split:
            focus_points = align_split_focus_points(focus_points)

        template_json = clear_template_text_objects(template_json)
        d_title = clean_title_for_render(job.design_title or "")
        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": d_title,
                "label": job.design_subtitle or "DISCUSSION",
                "watermark": fanpage.watermark_text or "",
                "watermark_image": watermark_datauri(fanpage),
                "image_srcs": image_srcs,
                "focus_points": focus_points,
                "image_zooms": image_zooms,
                "scale": settings.design_render_scale,
                "accent": fanpage.accent_color,
                "look": brand_look(fanpage, template_json),
            },
            timeout=_RENDER_TIMEOUT,
        )
        resp.raise_for_status()
        png_bytes = resp.content

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png_bytes)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.design_template_id = template.id
        job.last_image_marker = image_marker
        job.design_engine = "template"
        job.design_engine_note = None
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        if gallery_image:
            gallery_image.is_used = True
        db.commit()

        logger.info("Discussion: job %d rendered → %s (%d bytes)", job_id, filename, len(png_bytes))

        if fanpage.discussion_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Discussion: job %d render failed: %s", job_id, exc, exc_info=True)
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
        _release_queued_lock(job_id)
        db.close()


@celery_app.task(name="app.tasks.design_renderer.render_pinterest", bind=True, max_retries=2)
def render_pinterest(self, job_id: int):
    """Render a Mode 5 Pinterest card: the exact photo a consumed
    PinterestContentIdea was bound to (job.source_gallery_image_id) — no
    article, no photo search, unlike render_design/render_discussion.
    job.design_title/design_caption carry the idea's own title/description
    verbatim (see app/tasks/pinterest.py's _create_one) — no separate AI
    copywriting step, per the user's own flow: the idea's text IS the post's
    text. No dedicated Mode 5 template — reuses the Quote/News pools,
    picking between them per idea by whether the bound photo has a
    detected face (quote = has a face, news = doesn't). Every text field is
    sent in the render payload regardless of which ones that template
    actually defines (each is a no-op in the renderer when absent)."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode
        from app.models.gallery import GalleryImage

        claimed = (
            db.query(PublishJob)
            .filter(
                PublishJob.id == job_id,
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.pinterest_content,
            )
            .update({"status": PublishJobStatus.rendering}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return

        job = db.query(PublishJob).filter_by(id=job_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        gallery_image = (
            db.query(GalleryImage).filter_by(id=job.source_gallery_image_id).first()
            if job.source_gallery_image_id else None
        )
        if not fanpage or not gallery_image or not gallery_image.local_path or not os.path.exists(gallery_image.local_path):
            job.status = PublishJobStatus.pending_design
            job.last_error = "fanpage or source photo missing"
            db.commit()
            return

        from app.services.design_images import (
            resolve_template, prepare_design_images, focus_points_for, watermark_datauri,
            _safe_face_cy_ceiling, fix_unsafe_single_photo_face, photo_crops_well,
            _dominant_face_bbox,
        )

        image_bytes = Path(gallery_image.local_path).read_bytes()

        # No dedicated Mode 5 template setting — reuses the same Quote/News
        # pools Mode 2/3 already have, per the user's own call ("bisa
        # memilih template news atau quote, tergantung jenis kontennya").
        # A detected face reads as a quote-style portrait (name/badge
        # treatment); no face reads as a news-style scene (headline over
        # the full photo) — same signal photo_crops_well/
        # pick the category instead of gating a crop-time fix.
        from app.services.pinterest_classifier import classify_pinterest_content
        category = classify_pinterest_content(job.design_title, job.design_subtitle)
        template = resolve_template(db, category, fanpage=fanpage, job_template_id=job.design_template_id)
        if not template or not template.template_json:
            job.status = PublishJobStatus.pending_design
            job.last_error = f"no {category} template configured — create one in Template Designer"
            db.commit()
            logger.warning("Pinterest: job %d has no usable template", job_id)
            return

        # A photo that doesn't crop well onto this template (landscape, no
        # detected face — see photo_crops_well's docstring for why no
        # automatic fix was kept for that case) skips the template
        # entirely and posts directly: the (already-upscaled, see
        # image_downloader._fetch_and_store) source photo as its own post
        # image, title/description as plain post text — no crop, no
        # overlay. Per the user's own call for Mode 5: don't force a bad
        # crop onto a photo that was never going to fit.
        if not photo_crops_well(image_bytes, template.canvas_width, template.canvas_height):
            job.design_image_path = gallery_image.local_path
            job.design_image_url = gallery_image.public_url
            job.design_template_id = None
            job.last_image_marker = f"gallery:{gallery_image.id}"
            job.design_engine = "template"
            job.design_engine_note = None
            job.status = PublishJobStatus.pending_publish
            job.last_error = None
            gallery_image.is_used = True
            db.commit()
            logger.info(
                "Pinterest: job %d posted directly (photo doesn't crop well onto template %d)",
                job_id, template.id,
            )
            if fanpage.pinterest_publish_mode == PublishMode.auto:
                from app.tasks.publisher import publish_job
                publish_job.delay(job.id)
            return

        image_src = f"data:image/jpeg;base64,{base64.b64encode(image_bytes).decode()}"
        niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name

        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            job.design_title or "", niche, image_src,
            main_path=gallery_image.local_path, smart=False, expand=bool(fanpage.design_expand),
        )

        # Single photo only (smart=False never splits) — same face-safety
        # pass render_design/render_discussion apply, see their identical
        # blocks for why fix_unsafe_single_photo_face is needed alongside
        # the ceiling alone.
        safe_cy_ceiling = _safe_face_cy_ceiling(template_json, template.canvas_height)
        if image_srcs and image_srcs[0]:
            try:
                main_bytes = base64.b64decode(image_srcs[0].split(",", 1)[1])
                fixed = fix_unsafe_single_photo_face(
                    main_bytes, template.canvas_width, template.canvas_height, safe_cy_ceiling,
                )
                if fixed:
                    image_srcs[0] = fixed
            except Exception as exc:
                logger.warning("Pinterest: fix_unsafe_single_photo_face failed for job %d: %s", job_id, exc)
        focus_points = focus_points_for(image_srcs, for_split=False, safe_cy_ceiling=safe_cy_ceiling)

        # No render-style branch: the renderer only touches a
        # title/subtitle/caption/label slot when the template actually
        # defines that placeholderRole (a no-op otherwise, confirmed in
        # renderer/inject.js — each block is gated on `objectFound &&
        # value`), so sending all of them is safe regardless of which
        # roles this one dedicated Pinterest template happens to use.
        #
        # label intentionally omitted (2026-09-05, user request): Pinterest
        # has no dedicated template of its own — it reuses the News/Quote
        # pools (see classify_pinterest_content above), several of which
        # carry a label/labelBadge pill meant to say "NEWS"/"DISCUSSION".
        # This render used to hardcode "SPOTLIGHT" into that same slot
        # since Mode 5 was first built; inject.js hides the label AND its
        # badge together when no label value is sent (see its
        # `if (labelObj && label) ... else labelObj.set({visible:false})`
        # branch), so leaving this out cleanly removes the badge/pill from
        # Pinterest cards instead of showing a label that was never
        # actually about the reused template's own News/Discussion framing.
        template_json = clear_template_text_objects(template_json)
        d_title = clean_title_for_render(job.design_title or "")
        payload = {
            "template_json": template_json,
            "width": template.canvas_width,
            "height": template.canvas_height,
            "title": d_title,
            "subtitle": "",
            "caption": job.design_caption or "",
            "watermark": fanpage.watermark_text or "",
            "watermark_image": watermark_datauri(fanpage),
            "image_srcs": image_srcs,
            "focus_points": focus_points,
            "image_zooms": None,
            "scale": settings.design_render_scale,
            "accent": fanpage.accent_color,
            "look": brand_look(fanpage, template_json),
        }

        resp = httpx.post(f"{settings.renderer_url.rstrip('/')}/render", json=payload, timeout=_RENDER_TIMEOUT)
        resp.raise_for_status()
        png_bytes = resp.content

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png_bytes)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.design_template_id = template.id
        job.last_image_marker = f"gallery:{gallery_image.id}"
        job.design_engine = "template"
        job.design_engine_note = None
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        gallery_image.is_used = True
        db.commit()

        logger.info("Pinterest: job %d rendered → %s (%d bytes)", job_id, filename, len(png_bytes))

        if fanpage.pinterest_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Pinterest: job %d render failed: %s", job_id, exc, exc_info=True)
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


_FB_DISCUSSION_LABELS = ("DISCUSSION", "HOT TAKE")


def _news_badge_text(fanpage, breaking: bool = False) -> str:
    """The small pill badge above a News template's headline (e.g. "F1
    NEWS" or "BREAKING")."""
    if breaking:
        return "BREAKING"
    if getattr(fanpage, "mode2_badge_text", None):
        return fanpage.mode2_badge_text
    if getattr(fanpage, "mode2_gallery_niches", None):
        return f"{fanpage.mode2_gallery_niches[0]} NEWS"
    return ""


def _facebook_photo_category(db, job) -> str:
    """news | quote | discussion — the category _consume_one pinned via
    job.design_template_id, else inferred from the fields it filled (a
    discussion's subtitle is its label, a quote's is the speaker)."""
    from app.models.design_templates import DesignTemplate

    if job.design_template_id:
        tpl = db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
        if tpl and tpl.category in ("news", "quote", "discussion"):
            return tpl.category
    if (job.design_subtitle or "").upper() in _FB_DISCUSSION_LABELS:
        return "discussion"
    return "quote" if job.design_subtitle else "news"


def _facebook_photo_grounding(category: str, job) -> str:
    """Card text with the subject's name spliced in, for photo sourcing: a
    quote's title is the bare quote (the name lives in the speaker badge),
    a discussion's subject lives in design_caption — without the name the
    subject extraction guesses (see render_design's subject_title note)."""
    title = job.design_title or ""
    name = job.design_subtitle if category == "quote" else job.design_caption if category == "discussion" else None
    return f"{name.strip()}: {title}" if name and name.strip() else title


def _select_facebook_photo_image(db, job, category: str, niche: str, exclude_marker: str | None):
    """A clean photo for a recreated Mode 6 card — never the source page's
    graphic. Subject photo first (fresh gallery → fresh Getty → stale
    gallery, see source_news_main); news/discussion then fall back to a
    vision-verified topic search. A quote card must show its speaker, so it
    gets no topic fallback. Returns (datauri, GalleryImage|None, marker) or
    (None, None, None)."""
    from app.models.gallery import GalleryImage
    from app.services.design_images import source_news_main, fetch_topic_datauri

    excluded_path = None
    if exclude_marker and exclude_marker.startswith("gallery:"):
        excluded_path = (
            db.query(GalleryImage.local_path).filter_by(id=int(exclude_marker.split(":", 1)[1])).scalar()
        )

    grounding = _facebook_photo_grounding(category, job)
    try:
        src, path = source_news_main(db, grounding, niche, exclude_paths={excluded_path} if excluded_path else None)
        if src:
            gi = db.query(GalleryImage).filter_by(local_path=path).first() if path else None
            return src, gi, (f"gallery:{gi.id}" if gi else "search")
    except Exception as exc:
        logger.warning("Facebook photo: subject-photo lookup failed for job %d: %s", job.id, exc)

    if category == "quote":
        return None, None, None
    try:
        uri = fetch_topic_datauri(grounding, niche)
        if uri:
            return uri, None, "search"
    except Exception as exc:
        logger.warning("Facebook photo: topic-photo search failed for job %d: %s", job.id, exc)
    return None, None, None


def _pick_fitting_facebook_photo(db, job, template, category: str, niche: str):
    """Up to 3 candidates through the same face-fits + quality gate
    render_design uses, each retry excluding the last rejected photo."""
    from app.services.design_images import _safe_face_cy_ceiling, single_photo_face_fits

    safe_cy_ceiling = _safe_face_cy_ceiling(template.template_json, template.canvas_height)
    exclude_marker = job.last_image_marker
    for _attempt in range(3):
        src, gi, marker = _select_facebook_photo_image(db, job, category, niche, exclude_marker)
        if not src:
            return None, None, None
        try:
            fits = single_photo_face_fits(
                base64.b64decode(src.split(",", 1)[1]),
                template.canvas_width, template.canvas_height, safe_cy_ceiling,
            )
        except Exception:
            fits = True  # can't decode to check — don't block over a hiccup
        if fits:
            return src, gi, marker
        logger.info("Facebook photo: job %d rejected candidate %s — failed the face-fit/quality gate", job.id, marker)
        exclude_marker = marker
    return None, None, None


@celery_app.task(name="app.tasks.design_renderer.render_facebook_photo", bind=True, max_retries=2)
def render_facebook_photo(self, job_id: int):
    """Render a Mode 6 card, recreated like IG recreate: the text _consume_one
    already rewrote (news headline / quote + speaker / discussion question +
    label) on the fanpage's own news/quote/discussion template, around a
    clean photo of the subject. The source page's photo was only read for
    its text and is never used here — no photo found means the job waits
    for a manual image rather than falling back to it."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode

        claimed = (
            db.query(PublishJob)
            .filter(
                PublishJob.id == job_id,
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.facebook_recreate,
            )
            .update({"status": PublishJobStatus.rendering}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return

        job = db.query(PublishJob).filter_by(id=job_id).first()
        job.design_not_before = None
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        if not fanpage:
            job.status = PublishJobStatus.pending_design
            job.last_error = "fanpage missing"
            db.commit()
            return

        article = None
        if fanpage.fb_visual_engine == "chatgpt" and job.source_article_id:
            from app.models.scraped_articles import ScrapedArticle
            article = db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()

        from app.services.design_images import (
            resolve_template, prepare_design_images, focus_points_for, watermark_datauri,
            align_split_focus_points, find_role_object, _safe_face_cy_ceiling,
            fix_unsafe_single_photo_face,
        )

        category = _facebook_photo_category(db, job)
        template = resolve_template(db, category, fanpage=fanpage, job_template_id=job.design_template_id)
        if (not template or not template.template_json) and category != "news":
            # Same graceful fallback as render_discussion: no quote/discussion
            # template anywhere → the card still goes out on the News layout.
            template = resolve_template(db, "news", fanpage=fanpage)
        if not template or not template.template_json:
            job.status = PublishJobStatus.pending_design
            job.last_error = f"no {category} or news template configured — create one in Template Designer"
            db.commit()
            logger.warning("Facebook photo: job %d has no usable template", job_id)
            return

        niche = (fanpage.mode2_gallery_niches or [None])[0] or fanpage.name
        speaker_gallery_images = []
        from app.services.visual_engine import VisualEngineDeferred
        try:
            speaker_design = _maybe_compose_speaker_design(db, fanpage, job, article, mode="facebook")
        except VisualEngineDeferred as exc:
            _defer_job(db, job, exc)
            return
        if speaker_design:
            speaker_gallery_images = getattr(speaker_design, "used_gallery_images", [])
            image_src, gallery_image, image_marker = (
                f"data:image/png;base64,{base64.b64encode(speaker_design).decode()}",
                None,
                "speaker_design",
            )
        else:
            image_src, gallery_image, image_marker = _pick_fitting_facebook_photo(db, job, template, category, niche)
        if not image_src:
            job.status = PublishJobStatus.pending_design
            job.last_error = f"needs manual image — no clean {category} photo found for this card"
            db.commit()
            logger.warning("Facebook photo: job %d needs a manual image", job_id)
            return

        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            _facebook_photo_grounding(category, job), niche, image_src,
            main_path=gallery_image.local_path if gallery_image else None,
            expand=bool(fanpage.design_expand),
        )
        image_zooms = template_json.pop("_splitImageZooms", None)

        image_2_slot = find_role_object(template_json, "image_2")
        is_split = (
            image_2_slot is not None and image_2_slot.get("type") == "rect"
            and len(image_srcs) >= 2 and image_srcs[0] and image_srcs[1]
        )
        safe_cy_ceiling = _safe_face_cy_ceiling(template_json, template.canvas_height)
        # See render_design's identical block (zero cover-fit vertical slack
        # on a landscape photo — the ceiling alone can't be honored).
        if not is_split and image_srcs and image_srcs[0]:
            try:
                main_bytes = base64.b64decode(image_srcs[0].split(",", 1)[1])
                fixed = fix_unsafe_single_photo_face(
                    main_bytes, template.canvas_width, template.canvas_height, safe_cy_ceiling,
                )
                if fixed:
                    image_srcs[0] = fixed
            except Exception as exc:
                logger.warning("Facebook photo: fix_unsafe_single_photo_face failed for job %d: %s", job_id, exc)
        focus_points = focus_points_for(image_srcs, for_split=False, safe_cy_ceiling=safe_cy_ceiling)
        if is_split:
            focus_points = align_split_focus_points(focus_points)

        # Field contract per category: a quote's speaker is the subtitle
        # (name badge); a discussion's subtitle is its label and its caption
        # is the subject (used for the photo only, never rendered); the News
        # pill badge only on news cards.
        if category == "discussion":
            subtitle, label = "", job.design_subtitle or "DISCUSSION"
        elif category == "quote":
            subtitle, label = job.design_subtitle or "", ""
        else:
            subtitle, label = "", _news_badge_text(fanpage)

        template_json = clear_template_text_objects(template_json)
        d_title = clean_title_for_render(job.design_title or "")
        sub_text, cap_text = _speaker_line_fields(template_json, subtitle, "")
        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": d_title,
                "subtitle": sub_text,
                "caption": cap_text,
                "label": label,
                "watermark": fanpage.watermark_text or "",
                "watermark_image": watermark_datauri(fanpage),
                "image_srcs": image_srcs,
                "focus_points": focus_points,
                "image_zooms": image_zooms,
                "scale": settings.design_render_scale,
                "accent": fanpage.accent_color,
                "look": brand_look(fanpage, template_json),
            },
            timeout=_RENDER_TIMEOUT,
        )
        resp.raise_for_status()
        png_bytes = resp.content

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png_bytes)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.design_template_id = template.id
        job.last_image_marker = image_marker
        if image_marker == "speaker_design":
            job.design_engine = getattr(speaker_design, "engine", None) or "codex"
            note = getattr(speaker_design, "engine_note", "") or None
            job.design_engine_note = note[:200] if note else None
        else:
            job.design_engine = "template"
            job.design_engine_note = (
                "AI image failed → template"
                if getattr(fanpage, "fb_visual_engine", None) == "chatgpt"
                else None
            )
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        if gallery_image:
            gallery_image.is_used = True
            gallery_image.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        for s_gi in speaker_gallery_images:
            s_gi.is_used = True
            s_gi.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        logger.info(
            "Facebook photo: job %d (%s) rendered → %s (%d bytes)", job_id, category, filename, len(png_bytes),
        )

        if fanpage.facebook_photo_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Facebook photo: job %d render failed: %s", job_id, exc, exc_info=True)
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
        _release_queued_lock(job_id)
        db.close()


# How long a job that already failed with "needs manual image" sits out
# before this sweep will retry it automatically again. Without this, a
# subject with no findable photo (select_image_for_job /
# render_discussion both just set status back to pending_design — see
# their "needs a manual image" branches) never leaves this query's result
# set, so every ~2min tick re-ran the full (expensive, paid Getty/gallery
# search) photo lookup for the exact same doomed-to-fail job forever —
# found 2026-08-20 via 9 "Fight Today" discussion jobs stuck retrying in a
# loop, each attempt burning real web/fetch spend for zero progress and
# crowding the shared 10-per-sweep slot budget other fanpages' genuinely
# new jobs need. A human can still jump the cooldown any time via "Open in
# Designer" in the Queue, which attaches a photo directly.
_NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS = 6


def _defer_job(db, job, exc) -> None:
    """Night deferral (gpt-cli plan B10): no image engine may run until exc.until. Back to pending_design;
    render_pending_designs dispatches it again after design_not_before. Does not use a Celery retry."""
    from app.models.publish_jobs import PublishJobStatus
    job.status = PublishJobStatus.pending_design
    job.design_not_before = exc.until.astimezone(timezone.utc).replace(tzinfo=None)
    job.last_error = f"AI image deferred until {exc.until.isoformat()} ({exc.reason})"
    db.commit()


@celery_app.task(name="app.tasks.design_renderer.render_pending_designs")
def render_pending_designs():
    """Sweep: auto-render pending_design jobs for fanpages in auto mode."""
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import or_

        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode

        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        stale_cutoff = now_naive - timedelta(hours=NEWS_STALE_HOURS)
        retry_cutoff = now_naive - timedelta(
            hours=_NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS
        )
        not_deferred = or_(
            PublishJob.design_not_before.is_(None),
            PublishJob.design_not_before <= now_naive,
        )
        # A job stuck on "needs manual image" only re-qualifies once its
        # last attempt is older than the cooldown; anything else (a job
        # that's never been attempted, or failed for some other reason)
        # is untouched by this and still picked up every sweep as before.
        not_stuck_on_missing_image = or_(
            PublishJob.last_error.is_(None),
            ~PublishJob.last_error.ilike("needs manual image%"),
            PublishJob.updated_at < retry_cutoff,
        )

        stale_jobs = (
            db.query(PublishJob)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.news_content,
                TargetFanpage.mode2_publish_mode == PublishMode.auto,
                PublishJob.created_at < stale_cutoff,
            )
            .all()
        )
        if stale_jobs:
            for sj in stale_jobs:
                sj.status = PublishJobStatus.skipped
                sj.last_error = "stale: news older than 24 h, not rendered"
            db.commit()
            logger.info("Marked %d stale news jobs as skipped (older than %dh)", len(stale_jobs), NEWS_STALE_HOURS)

        jobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.news_content,
                TargetFanpage.mode2_publish_mode == PublishMode.auto,
                PublishJob.created_at >= stale_cutoff,
                not_stuck_on_missing_image,
                not_deferred,
            )
            .limit(10)
            .all()
        )
        from app.services.engine_router import render_queue_for

        for (job_id,) in jobs:
            if not _acquire_queued_lock(job_id):
                continue
            job = db.query(PublishJob).filter_by(id=job_id).first()
            fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first() if job else None
            q = render_queue_for(db, fanpage, "news") if fanpage else None
            if q:
                render_design.apply_async(args=[job_id], queue=q, expires=1800)
            else:
                render_design.delay(job_id)

        # Mode 4 discussion cards: always auto-render regardless of publish
        # mode — unlike news_content, discussion has no manual Designer-canvas
        # path (design-payload/design-image don't support it), so this sweep
        # is the only way a card ever gets its image. render_discussion itself
        # gates auto-*publish* on discussion_publish_mode; manual_review
        # fanpages get the rendered card parked in pending_publish for a human
        # to approve in the Queue instead of never rendering at all.
        djobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.discussion,
                not_stuck_on_missing_image,
            )
            .limit(10)
            .all()
        )
        for (job_id,) in djobs:
            if not _acquire_queued_lock(job_id):
                continue
            render_discussion.delay(job_id)

        # Mode 5 Pinterest cards: same "always auto-render" reasoning as
        # discussion — no manual Designer-canvas path, render_pinterest
        # itself gates auto-*publish* on pinterest_publish_mode.
        pjobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.pinterest_content,
                not_stuck_on_missing_image,
            )
            .limit(10)
            .all()
        )
        for (job_id,) in pjobs:
            render_pinterest.delay(job_id)

        # Mode 6 Facebook-photo cards: same "always auto-render" reasoning as
        # Pinterest. _consume_one only dispatches the render itself when the
        # fanpage is auto, so without this a manual_review fanpage's jobs
        # would never get an image at all.
        fjobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.facebook_recreate,
                not_stuck_on_missing_image,
                not_deferred,
            )
            .limit(10)
            .all()
        )
        for (job_id,) in fjobs:
            if not _acquire_queued_lock(job_id):
                continue
            job = db.query(PublishJob).filter_by(id=job_id).first()
            fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first() if job else None
            q = render_queue_for(db, fanpage, "facebook") if fanpage else None
            if q:
                render_facebook_photo.apply_async(args=[job_id], queue=q, expires=1800)
            else:
                render_facebook_photo.delay(job_id)

        ijobs = (
            db.query(PublishJob.id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.ig_recreate,
                PublishJob.design_not_before.isnot(None),
                PublishJob.design_not_before <= now_naive,
            )
            .limit(10)
            .all()
        )
        for (job_id,) in ijobs:
            if not _acquire_queued_lock(job_id):
                continue
            from app.tasks.ig_recreate import render_ig_recreate
            render_ig_recreate.apply_async((job_id,), queue="visual", expires=1800)

        if jobs or djobs or pjobs or fjobs or ijobs:
            logger.info(
                "Design sweep: dispatched %d news + %d discussion + %d pinterest + %d facebook-photo + %d ig-recreate renders",
                len(jobs), len(djobs), len(pjobs), len(fjobs), len(ijobs),
            )
    finally:
        db.close()


# Generous relative to the actual render pipeline: a single /render HTTP
# call times out at _RENDER_TIMEOUT=120s, but the FULL task (subject/photo
# search, several vision-verification calls) has been observed taking up
# to ~600s/10min for a genuinely slow one — long enough that this sweep
# never touches a render that's actually still in progress, short enough
# that a real orphan doesn't sit for days.
_STALE_RENDERING_MINUTES = 30


@celery_app.task(name="app.tasks.design_renderer.recover_stuck_renders")
def recover_stuck_renders():
    """Reset jobs stuck in 'rendering' for longer than any real render
    could legitimately take, back to 'pending_design' so the ordinary
    render_pending_designs sweep above picks them up again.

    render_design/render_discussion/render_pinterest's own except blocks
    already release a job back to pending_design when they catch a Python
    exception (a bad candidate photo, a renderer HTTP error, etc.) — but
    that cleanup code only runs if the TASK ITSELF gets a chance to run at
    all. A worker process getting killed mid-task (the ordinary case: a
    deploy's `docker compose up -d --remove-orphans` recreating the worker
    container while it's mid-render) leaves nothing to ever run that
    cleanup — the job is orphaned in 'rendering' forever, with no other
    recovery path anywhere in the codebase before this.

    Real incident, 2026-09-01: investigating a user report of unusually
    few published posts on a normal day led to finding 12 jobs stuck this
    way, the oldest from 2026-08-22 (over a week earlier) — this had
    apparently been happening silently for a while, one job at a time,
    with nothing to ever surface or fix it short of someone manually
    querying `status='rendering'` and noticing. This sweep exists so that
    investigation doesn't need to happen by hand again."""
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta, timezone

        from app.models.publish_jobs import PublishJob, PublishJobStatus

        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=_STALE_RENDERING_MINUTES)
        stuck = (
            db.query(PublishJob)
            .filter(PublishJob.status == PublishJobStatus.rendering, PublishJob.updated_at < cutoff)
            .all()
        )
        for job in stuck:
            job.status = PublishJobStatus.pending_design
            job.last_error = "Recovered from a stuck 'rendering' state (likely an interrupted worker) — retried automatically."
        if stuck:
            db.commit()
            logger.info(
                "Recovery: reset %d job(s) stuck in 'rendering' for >%dm back to pending_design",
                len(stuck), _STALE_RENDERING_MINUTES,
            )
    finally:
        db.close()
