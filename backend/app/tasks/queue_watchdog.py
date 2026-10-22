"""Alert the owner on Telegram when a Celery queue backs up (a stuck worker)."""
import logging

import redis

from app.config import get_settings
from app.database import SessionLocal
from app.tasks.celery_app import celery_app
from app.tasks.phone_watchdog import send_telegram_fallback

logger = logging.getLogger(__name__)

# Waiting-task limits per queue; normal levels are 0-30 on the default queue.
QUEUE_BACKLOG_LIMITS = {"celery": 200, "crawl": 50, "visual": 100, "video": 50, "gallery": 300}
QUEUE_WORKERS = {
    "celery": "worker",
    "crawl": "worker-crawl",
    "visual": "worker-visual",
    "video": "worker-video",
    "gallery": "worker-gallery",
}
ALERT_COOLDOWN_SECONDS = 3600
ALERT_KEY = "queue_watchdog:alerted:{queue}"


def backlog_over_limits(r) -> dict[str, int]:
    """Return {queue: length} for queues whose waiting tasks exceed limit."""
    over: dict[str, int] = {}
    for queue, limit in QUEUE_BACKLOG_LIMITS.items():
        try:
            length = r.llen(queue)
            if length > limit:
                over[queue] = length
        except Exception as exc:
            logger.warning("Failed to check llen for queue '%s': %s", queue, exc)
    return over


@celery_app.task(name="app.tasks.queue_watchdog.check_queue_backlog", ignore_result=True)
def check_queue_backlog() -> dict[str, int]:
    """Check Celery queue lengths and alert if any queue is backed up."""
    try:
        r = redis.from_url(get_settings().redis_url, decode_responses=True)
        over = backlog_over_limits(r)
        for q, n in over.items():
            limit = QUEUE_BACKLOG_LIMITS.get(q, 0)
            logger.error("Queue backlog: %s has %d waiting tasks (limit %d)", q, n, limit)
            if r.set(ALERT_KEY.format(queue=q), "1", nx=True, ex=ALERT_COOLDOWN_SECONDS):
                worker = QUEUE_WORKERS[q]
                message = (
                    f"Studio alert: Celery queue '{q}' has {n} waiting tasks (limit {limit}). "
                    f"Worker '{worker}' is probably stuck — check `docker logs studio-{worker}-1` and restart it."
                )
                db = SessionLocal()
                try:
                    send_telegram_fallback(message, db)
                finally:
                    db.close()
        return over
    except Exception as exc:
        logger.warning("Error in check_queue_backlog: %s", type(exc).__name__)
        return {}
