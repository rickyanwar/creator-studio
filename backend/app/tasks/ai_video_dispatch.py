"""Celery task for sweeping AI video jobs."""

from app.database import SessionLocal
from app.services import ai_video_dispatch
from app.tasks.celery_app import celery_app
from app.utils.logger import get_logger

logger = get_logger(__name__)


@celery_app.task(name="app.tasks.ai_video_dispatch.sweep_ai_video_jobs")
def sweep_ai_video_jobs():
    """Sweep expired leases and stale pinned video jobs."""
    db = SessionLocal()
    try:
        ai_video_dispatch.sweep(db)
    except Exception as exc:
        logger.exception("Error during sweep_ai_video_jobs: %s", exc)
    finally:
        db.close()
