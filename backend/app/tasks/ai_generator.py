"""AI Caption Generator task."""

import logging

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

        prompt = build_caption_prompt(
            fanpage=fanpage,
            source_username=ig_source.ig_username,
            original_caption=post.original_caption or "",
        )

        caption, provider_used = generate_caption(prompt, force_provider=force_provider)

        job.ai_generated_caption = caption
        job.ai_provider_used = AIProvider(provider_used)

        if fanpage.publish_mode == PublishMode.auto:
            job.status = PublishJobStatus.pending_publish
            db.commit()
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)
        else:
            job.status = PublishJobStatus.pending_review
            db.commit()

        logger.info(
            "Job %d: caption generated via %s (fanpage=%s, mode=%s)",
            job_id, provider_used, fanpage.name, fanpage.publish_mode,
        )

    except Exception as exc:
        db.rollback()
        logger.error("Caption generation failed for job %d: %s", job_id, exc, exc_info=True)
        from app.models.publish_jobs import PublishJob, PublishJobStatus
        job = db.query(PublishJob).filter_by(id=job_id).first()
        if job:
            job.last_error = str(exc)
            job.attempt_count = (job.attempt_count or 0) + 1
            db.commit()
        raise self.retry(exc=exc, countdown=120)
    finally:
        db.close()
