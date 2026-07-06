"""Per-fanpage image watermark task — stamps a fanpage's own watermark text
onto the (already source-level cleaned) post images before caption generation.
"""

import logging
from pathlib import Path

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="app.tasks.image_watermark.apply_watermark_for_job", bind=True, max_retries=8)
def apply_watermark_for_job(self, job_id: int):
    """Stamp fanpage.watermark_text onto post images, store as a per-job image variant."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus
        from app.models.target_fanpages import TargetFanpage
        from app.models.posts import Post
        from app.services.ai_image_edit import build_watermark_prompt, edit_image

        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job or job.status != PublishJobStatus.pending_watermark:
            return

        post = db.query(Post).filter_by(id=job.post_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()

        if not fanpage.watermark_text or not post.image_local_paths:
            job.status = PublishJobStatus.pending_caption
            db.commit()
        else:
            job_dir = Path(settings.storage_base_path) / "posts" / str(post.uuid) / "jobs" / str(job.id)
            job_dir.mkdir(parents=True, exist_ok=True)

            prompt = build_watermark_prompt(fanpage.watermark_text)
            watermarked_urls = []
            for idx, local_path in enumerate(post.image_local_paths):
                with open(local_path, "rb") as f:
                    original_bytes = f.read()
                edited_bytes = edit_image(original_bytes, prompt)

                filename = f"{idx}.jpg"
                dest = job_dir / filename
                with open(dest, "wb") as f:
                    f.write(edited_bytes)

                public_url = f"{settings.storage_base_url.rstrip('/')}/posts/{post.uuid}/jobs/{job.id}/{filename}"
                watermarked_urls.append(public_url)

            job.watermarked_image_urls = watermarked_urls
            job.status = PublishJobStatus.pending_caption
            db.commit()

            logger.info("Job %d: watermarked %d images for fanpage %s", job_id, len(watermarked_urls), fanpage.name)

        from app.tasks.ai_generator import generate_caption_for_job
        generate_caption_for_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.warning("Job %d: watermark failed, will retry: %s", job_id, exc)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


@celery_app.task(name="app.tasks.image_watermark.recover_stuck_watermarks")
def recover_stuck_watermarks():
    """Re-trigger watermarking for jobs stuck in 'pending_watermark' too long.

    Runs every 30 minutes to catch cases where retries were exhausted or
    the task was dropped from Redis mid-chain.
    """
    from datetime import datetime, timezone, timedelta
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    db = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

        stuck_jobs = (
            db.query(PublishJob.id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_watermark,
                PublishJob.updated_at < cutoff,
            )
            .all()
        )

        for (job_id,) in stuck_jobs:
            apply_watermark_for_job.delay(job_id)
            logger.warning("Recovery: re-queued watermark for stuck job %d", job_id)

    finally:
        db.close()
