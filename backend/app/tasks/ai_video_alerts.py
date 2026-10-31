"""Celery tasks for Google Flow operator alerts."""
import logging
from app.database import SessionLocal
from app.tasks.celery_app import celery_app
from app.tasks.prompt_polish_watchdog import _send_notification

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ai_video_alerts.send_gflow_alert", ignore_result=True)
def send_gflow_alert(message: str, dedupe_key: str) -> None:
    db = SessionLocal()
    try:
        _send_notification(message, dedupe_key=dedupe_key, db=db)
    finally:
        db.close()
