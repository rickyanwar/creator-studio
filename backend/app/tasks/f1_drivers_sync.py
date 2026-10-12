import logging
import httpx
from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.services.f1_drivers_sync import fetch_openf1_drivers, sync_f1_drivers

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.f1_drivers_sync.sync_f1_drivers_task")
def sync_f1_drivers_task():
    try:
        with httpx.Client(timeout=15.0) as client:
            drivers = fetch_openf1_drivers(client)
    except Exception as exc:
        logger.warning(f"Failed to fetch drivers from OpenF1: {exc}")
        return {"status": "error", "error": str(exc)}

    db = SessionLocal()
    try:
        counts = sync_f1_drivers(db, drivers)
        logger.info(f"OpenF1 drivers sync completed: {counts}")
        return counts
    except Exception as exc:
        db.rollback()
        logger.warning(f"Failed to sync F1 drivers into DB: {exc}")
        return {"status": "error", "error": str(exc)}
    finally:
        db.close()
