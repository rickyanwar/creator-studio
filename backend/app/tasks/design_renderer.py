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
import uuid
from pathlib import Path

import httpx

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_RENDER_TIMEOUT = 120.0


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
        src, path = source_news_main(db, title, niche, exclude_path=excluded_local_path)
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

        # Two-slot templates also get a secondary photo (inset/split — see
        # design_images.prepare_design_images) with face-aware focus crops.
        title = job.design_title or article.scraped_title
        # extract_two_subjects/extract_secondary_subject (called inside
        # prepare_design_images) need a real name to reason about. A
        # quote-category job's title is DELIBERATELY the bare quote with no
        # name in it (news_copywriter.py's prompt: "Do NOT include the
        # speaker's name here" — the name goes in design_subtitle's name
        # badge instead), so passing just `title` here starves the model of
        # any subject signal and it hallucinates a plausible-sounding pair
        # instead of correctly answering NONE/one-person — real incident,
        # 2026-08-25: job 4796, a George Russell quote about a Mercedes
        # team-order swap, paired with Lando Norris/Oscar Piastri photos —
        # neither of whom is in the story. Splicing the name badge and the
        # original scraped headline in ahead of the bare title gives it real
        # names to ground on whenever they exist; `title` itself (the exact
        # on-card text) is untouched below for the actual render payload.
        subject_title = " — ".join(
            p for p in (job.design_subtitle, article.scraped_title, title) if p
        ) or title
        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            subject_title, fanpage.name, image_src,
            main_path=gallery_image.local_path if gallery_image else None,
            expand=bool(fanpage.design_expand),
        )
        # See build_split_srcs / prepare_design_images's split-flow branch —
        # the corrective per-photo zoom is stashed here rather than widening
        # prepare_design_images's return signature (every other branch there
        # returns a plain 2-tuple).
        image_zooms = template_json.pop("_splitImageZooms", None)

        # A rectangular image_2 with both slots filled means prepare_design_images
        # took the split flow (see its "Split flow" branch). Both photos DO
        # have an overlay on them — the real template's scrim gradient is
        # baked over the bottom ~40% of the full-height split photo (not a
        # separate non-overlapping band) — so this is exactly the situation
        # for_split=False's upward face-lift bias exists for; for_split=True
        # is for the OLD flat-band-below-photo design where nothing is drawn
        # over the photo at all. See focus_points_for's docstring — this was
        # backwards until 2026-08-21 (found via a real render: faces landing
        # under the scrim with for_split=True's no-lift behavior).
        image_2_slot = find_role_object(template_json, "image_2")
        is_split = (
            image_2_slot is not None and image_2_slot.get("type") == "rect"
            and len(image_srcs) >= 2 and image_srcs[0] and image_srcs[1]
        )
        # The chosen template's own title-box position, not a flat guess —
        # see _safe_face_cy_ceiling's docstring (found 2026-08-24 via 5 real
        # renders where the face landed under the title text on templates
        # whose title starts much higher than the old flat 0.44/0.7 ceiling
        # assumed).
        safe_cy_ceiling = _safe_face_cy_ceiling(template_json, template.canvas_height)
        # Single-photo-only safety net: a landscape source on this canvas's
        # portrait aspect has ZERO cover-fit vertical slack (confirmed via
        # real-number testing 2026-08-24 — corrective zoom, the split fix's
        # approach, does NOT work here, see fix_unsafe_single_photo_face's
        # docstring for why), so the ceiling above can compute the right
        # target but the renderer has no room to actually honor it. Content-
        # aware-fills the photo instead when that's genuinely the case; a
        # no-op (returns None, image_srcs[0] left untouched) for the common
        # case where real slack already exists or the face is already safe.
        if not is_split and image_srcs and image_srcs[0]:
            try:
                main_bytes = base64.b64decode(image_srcs[0].split(",", 1)[1])
                fixed = fix_unsafe_single_photo_face(
                    main_bytes, template.canvas_width, template.canvas_height, safe_cy_ceiling,
                )
                if fixed:
                    image_srcs[0] = fixed
            except Exception as exc:
                logger.warning("Design: fix_unsafe_single_photo_face failed for job %d: %s", job_id, exc)
        focus_points = focus_points_for(image_srcs, for_split=False, safe_cy_ceiling=safe_cy_ceiling)
        if is_split:
            focus_points = align_split_focus_points(focus_points)

        # ── Render via Puppeteer + Fabric.js service ──
        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": title,
                "subtitle": job.design_subtitle or "",
                "caption": job.design_caption or "",
                # No fallback to fanpage name/username — a fanpage that hasn't
                # set an explicit watermark_text/watermark_image gets NO
                # watermark on the design at all.
                "watermark": fanpage.watermark_text or "",
                "watermark_image": watermark_datauri(fanpage),
                "image_srcs": image_srcs,
                "focus_points": focus_points,
                "image_zooms": image_zooms,
                "scale": settings.design_render_scale,
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
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        if gallery_image:
            gallery_image.is_used = True
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
                    uri = fetch_subject_datauri(db, subject, "face", niche)
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

        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": job.design_title or "",
                "label": job.design_subtitle or "DISCUSSION",
                "watermark": fanpage.watermark_text or "",
                "watermark_image": watermark_datauri(fanpage),
                "image_srcs": image_srcs,
                "focus_points": focus_points,
                "image_zooms": image_zooms,
                "scale": settings.design_render_scale,
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
        # fix_unsafe_single_photo_face already compute, just used here to
        # pick the category instead of gating a crop-time fix.
        has_quote = any(q in (job.design_title or "") for q in ['"', '“', '”'])
        category = "quote" if has_quote else "news"
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
        payload = {
            "template_json": template_json,
            "width": template.canvas_width,
            "height": template.canvas_height,
            "title": job.design_title or "",
            "subtitle": "",
            "caption": job.design_caption or "",
            "label": "SPOTLIGHT",
            "watermark": fanpage.watermark_text or "",
            "watermark_image": watermark_datauri(fanpage),
            "image_srcs": image_srcs,
            "focus_points": focus_points,
            "image_zooms": None,
            "scale": settings.design_render_scale,
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


@celery_app.task(name="app.tasks.design_renderer.render_pending_designs")
def render_pending_designs():
    """Sweep: auto-render pending_design jobs for fanpages in auto mode."""
    db = SessionLocal()
    try:
        from datetime import datetime, timedelta, timezone

        from sqlalchemy import or_

        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode

        retry_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            hours=_NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS
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

        jobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.news_content,
                TargetFanpage.mode2_publish_mode == PublishMode.auto,
                not_stuck_on_missing_image,
            )
            .limit(10)
            .all()
        )
        for (job_id,) in jobs:
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

        if jobs or djobs or pjobs:
            logger.info(
                "Design sweep: dispatched %d news + %d discussion + %d pinterest renders",
                len(jobs), len(djobs), len(pjobs),
            )
    finally:
        db.close()
