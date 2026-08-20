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
        image_src, gallery_image, image_marker = select_image_for_job(
            db, job, fanpage, article, exclude_marker=job.last_image_marker,
        )
        if not image_src:
            job.status = PublishJobStatus.pending_design
            job.last_error = "needs manual image — no gallery match and no article hero image"
            db.commit()
            logger.warning("Design: job %d needs a manual image", job_id)
            return

        # Two-slot templates also get a secondary photo (inset/split — see
        # design_images.prepare_design_images) with face-aware focus crops.
        from app.services.design_images import prepare_design_images, focus_points_for, watermark_datauri
        title = job.design_title or article.scraped_title
        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            title, fanpage.name, image_src,
            main_path=gallery_image.local_path if gallery_image else None,
            expand=bool(fanpage.design_expand),
        )

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
                "focus_points": focus_points_for(image_srcs),
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

        # Single-slot template → smart secondary sourcing off; still honour expand.
        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            job.design_title or "", niche, image_src,
            main_path=gallery_image.local_path if gallery_image else None,
            smart=False, expand=bool(fanpage.design_expand),
        )

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
                "focus_points": focus_points_for(image_srcs),
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

        if jobs or djobs:
            logger.info(
                "Design sweep: dispatched %d news + %d discussion renders",
                len(jobs), len(djobs),
            )
    finally:
        db.close()
