"""Fan-out task — creates one PublishJob per active fanpage for a stored post."""

import logging
import random

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

# Stagger between fanpages sharing the same IG source (seconds)
_FANPAGE_STAGGER_MIN = 60
_FANPAGE_STAGGER_MAX = 120

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
        slot = 0  # stagger slot index for fanpages sharing this source
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

            # Stagger caption generation (and therefore publishing) so fanpages
            # sharing the same IG source don't all post at exactly the same time.
            stagger = slot * random.randint(_FANPAGE_STAGGER_MIN, _FANPAGE_STAGGER_MAX)
            generate_caption_for_job.apply_async(args=[job.id], countdown=stagger)
            if stagger:
                logger.info(
                    "Job %d (fanpage=%d) caption delayed %ds to avoid simultaneous posting",
                    job.id, link.fanpage_id, stagger,
                )
            slot += 1

        post.status = PostStatus.pending_fanout
        db.commit()

        logger.info("Post %d: created %d publish jobs for %d fanpages", post_id, created, len(fanpage_links))

    except Exception as exc:
        db.rollback()
        logger.error("Fan-out error for post %d: %s", post_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
