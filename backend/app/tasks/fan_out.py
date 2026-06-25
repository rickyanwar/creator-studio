"""Fan-out task — creates one PublishJob per active fanpage for a stored post."""

import logging

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.fan_out.create_fanout_jobs", bind=True, max_retries=2)
def create_fanout_jobs(self, post_id: int):
    """Find all active fanpages sourcing this post's IG account and create PublishJobs."""
    db = SessionLocal()
    try:
        from app.models.posts import Post, PostStatus
        from app.models.fanpage_sources import FanpageSource
        from app.models.target_fanpages import TargetFanpage
        from app.models.publish_jobs import PublishJob, PublishJobStatus

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            return

        # Find active fanpages that subscribe to this IG source
        fanpage_links = (
            db.query(FanpageSource)
            .join(TargetFanpage, TargetFanpage.id == FanpageSource.fanpage_id)
            .filter(
                FanpageSource.ig_source_id == post.ig_source_id,
                FanpageSource.is_active == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
            )
            .all()
        )

        created = 0
        for link in fanpage_links:
            # Idempotency: skip if job already exists
            existing = db.query(PublishJob).filter_by(
                post_id=post_id, fanpage_id=link.fanpage_id
            ).first()
            if existing:
                continue

            job = PublishJob(
                post_id=post_id,
                fanpage_id=link.fanpage_id,
                status=PublishJobStatus.pending_caption,
            )
            db.add(job)
            db.flush()
            created += 1

            from app.tasks.ai_generator import generate_caption_for_job
            db.commit()
            generate_caption_for_job.delay(job.id)

        post.status = PostStatus.pending_fanout
        db.commit()

        logger.info("Post %d: created %d publish jobs for %d fanpages", post_id, created, len(fanpage_links))

    except Exception as exc:
        db.rollback()
        logger.error("Fan-out error for post %d: %s", post_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
