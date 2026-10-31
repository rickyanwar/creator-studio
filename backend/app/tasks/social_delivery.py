"""Celery task for periodic social delivery dispatch (S1b)."""

import logging
from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.services.social_delivery_dispatch import (
    dispatch_due_deliveries,
    follow_up_uncertain_phone_post,
)

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.social_delivery.dispatch_social_deliveries")
def dispatch_social_deliveries():
    """Periodic sweep (every 60s) to dispatch due and scheduled social deliveries."""
    db = SessionLocal()
    try:
        processed = dispatch_due_deliveries(db)
        if processed:
            logger.info("Social delivery tick processed %d deliveries", len(processed))
    except Exception as exc:
        logger.exception("Error in dispatch_social_deliveries tick: %s", exc)
    finally:
        db.close()


@celery_app.task(name="app.tasks.social_delivery.follow_up_phone_posts")
def follow_up_phone_posts():
    """Periodic sweep (every 60s) to follow up uncertain phone deliveries."""
    db = SessionLocal()
    try:
        outcome = follow_up_uncertain_phone_post(db)
        if outcome and outcome != "no_due_deliveries":
            logger.info("Phone post follow-up result: %s", outcome)
    except Exception as exc:
        logger.exception("Error in follow_up_phone_posts tick: %s", exc)
    finally:
        db.close()

