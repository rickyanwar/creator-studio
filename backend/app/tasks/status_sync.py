"""Repliz Status Sync — polls GET /public/schedule/{id} every 5 minutes."""

import logging

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.status_sync.sync_pending_schedules")
def sync_pending_schedules():
    """Poll Repliz for jobs that are still in 'processing' state."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus
        from app.services.repliz_client import get_repliz_client_from_db

        # Jobs that were published to Repliz but we haven't confirmed final status
        jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.repliz_schedule_id != None,
                PublishJob.status == PublishJobStatus.published,
                PublishJob.repliz_response_json["status"].astext.in_(["processing", "pending"])
                if hasattr(PublishJob.repliz_response_json, "astext")
                else True,
            )
            .limit(50)
            .all()
        )

        if not jobs:
            return

        try:
            client = get_repliz_client_from_db(db)
        except RuntimeError:
            logger.warning("Repliz credentials not set — skipping status sync")
            return

        for job in jobs:
            try:
                data = client.get_schedule(job.repliz_schedule_id)
                if job.repliz_response_json != data:
                    job.repliz_response_json = data
                    db.add(job)
            except Exception as exc:
                logger.warning("Status sync failed for job %d: %s", job.id, exc)

        db.commit()
        logger.info("Status sync: checked %d jobs", len(jobs))

    finally:
        db.close()
