"""AI Caption Generator task."""

import logging
import random
from datetime import datetime, timezone

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ai_generator.generate_caption_for_job", bind=True, max_retries=2)
def generate_caption_for_job(self, job_id: int, force_provider: str | None = None):
    """Generate AI caption for a PublishJob and update its status."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, AIProvider
        from app.models.target_fanpages import TargetFanpage, PublishMode
        from app.models.posts import Post
        from app.models.ig_sources import IGSource
        from app.services.ai_caption import build_caption_prompt, generate_caption

        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job or job.status != PublishJobStatus.pending_caption:
            return

        post = db.query(Post).filter_by(id=job.post_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first()

        from app.services.caption_grounding import has_usable_caption, describe_image_for_caption
        from app.services.fanpage_caption import caption_settings, strip_credit_lines

        image_description = None
        orig_caption = post.original_caption or ""

        if not has_usable_caption(orig_caption):
            niches = getattr(fanpage, "mode2_gallery_niches", None) or []
            niche_hint = niches[0] if (niches and len(niches) > 0) else None

            img_input = None
            local_paths = getattr(post, "image_local_paths", None) or []
            if local_paths and len(local_paths) > 0 and local_paths[0]:
                import os
                if os.path.isfile(local_paths[0]):
                    img_input = local_paths[0]

            if img_input is None:
                public_urls = getattr(post, "image_public_urls", None) or []
                if public_urls and len(public_urls) > 0 and public_urls[0]:
                    try:
                        import httpx
                        resp = httpx.get(public_urls[0], timeout=20.0)
                        if resp.status_code == 200:
                            img_input = resp.content
                    except Exception as exc:
                        logger.warning("Job %d: failed to download image %s: %s", job.id, public_urls[0], exc)

            if img_input:
                image_description = describe_image_for_caption(img_input, niche_hint=niche_hint)

            if image_description is None:
                cs = caption_settings(fanpage)
                source_username = ig_source.ig_username if ig_source else ""
                parts = []
                if cs.use_attribution and source_username:
                    attr_line = cs.attribution_template.format(source_username=source_username)
                    parts.append(attr_line)

                tags = []
                for kw in (cs.must_include or []):
                    if kw:
                        t = kw if kw.startswith("#") else f"#{kw.replace(' ', '')}"
                        if t not in tags:
                            tags.append(t)
                if niche_hint:
                    nt = f"#{niche_hint.replace(' ', '')}"
                    if nt not in tags:
                        tags.append(nt)

                if tags and cs.hashtag_count > 0:
                    tags_str = " ".join(tags[:cs.hashtag_count])
                    parts.append(tags_str)

                job.ai_generated_caption = "\n\n".join(parts) if parts else ""
                job.status = PublishJobStatus.pending_review
                job.last_error = "caption needs review: source caption empty and image check failed"
                db.commit()
                logger.warning(
                    "Job %d: vision check failed for empty source caption — forced to pending_review",
                    job.id,
                )
                return

        prompt = build_caption_prompt(
            fanpage=fanpage,
            source_username=ig_source.ig_username,
            original_caption=orig_caption,
            source=ig_source,
            image_description=image_description,
        )

        caption, provider_used = generate_caption(prompt, force_provider=force_provider)

        if not caption_settings(fanpage).use_attribution:
            caption = strip_credit_lines(caption)

        job.ai_generated_caption = caption
        job.ai_provider_used = AIProvider(provider_used)

        if fanpage.publish_mode == PublishMode.auto:
            job.status = PublishJobStatus.pending_publish
            db.commit()

            from app.tasks.publisher import publish_job

            # If this fanpage has published before, add a random 2–3 min gap
            # so Repliz/Facebook doesn't see uploads arriving too close together.
            countdown = _gap_since_last_publish(db, job.fanpage_id)
            publish_job.apply_async(args=[job.id], countdown=countdown)

            if countdown:
                logger.info(
                    "Job %d queued for auto-publish in %ds (fanpage=%s)",
                    job.id, countdown, fanpage.name,
                )
        else:
            job.status = PublishJobStatus.pending_review
            db.commit()

        logger.info(
            "Job %d: caption generated via %s (fanpage=%s, mode=%s)",
            job_id, provider_used, fanpage.name, fanpage.publish_mode,
        )

    except Exception as exc:
        db.rollback()
        from app.services.ai_caption import GroqRateLimitError
        if isinstance(exc, GroqRateLimitError):
            logger.warning("Job %d: Groq rate limited — retrying in 60s", job_id)
            raise self.retry(exc=exc, countdown=60)
        logger.error("Caption generation failed for job %d: %s", job_id, exc, exc_info=True)
        try:
            from app.models.publish_jobs import PublishJob
            job = db.query(PublishJob).filter_by(id=job_id).first()
            if job:
                job.last_error = str(exc)
                job.attempt_count = (job.attempt_count or 0) + 1
                db.commit()
        except Exception:
            db.rollback()
        raise self.retry(exc=exc, countdown=120)
    finally:
        db.close()


def _gap_since_last_publish(db, fanpage_id: int) -> int:
    """
    Return a countdown in seconds before the next publish for this fanpage.
    - First-ever upload for this fanpage → 0 (publish immediately).
    - Fanpage has published before → random 2–3 min delay.
    """
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    last = (
        db.query(PublishJob.published_at)
        .filter(
            PublishJob.fanpage_id == fanpage_id,
            PublishJob.status == PublishJobStatus.published,
            PublishJob.published_at.isnot(None),
        )
        .order_by(PublishJob.published_at.desc())
        .first()
    )

    if last is None:
        # Never published before — no delay needed
        return 0

    return random.randint(2 * 60, 3 * 60)  # 120–180 seconds
