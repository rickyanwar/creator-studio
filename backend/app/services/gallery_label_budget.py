import logging
from datetime import datetime, timezone
import redis
from sqlalchemy import func
from app.config import get_settings
from app.models.gallery import GalleryImage

logger = logging.getLogger(__name__)

KEY_PREFIX = "gallery:label_budget"
KEY_TTL_SECONDS = 2 * 86400  # 2 days


def get_budget_redis_client():
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def _get_today_key(now_utc: datetime | None = None) -> str:
    now = now_utc or datetime.now(timezone.utc)
    return f"{KEY_PREFIX}:{now.strftime('%Y-%m-%d')}"


def count_labeled_today_db(db, now_utc: datetime | None = None) -> int:
    now = now_utc or datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day)
    return (
        db.query(GalleryImage)
        .filter(GalleryImage.labeled_at >= today_start)
        .count()
    )


def reserve(cap: int, db=None, redis_client=None, now_utc: datetime | None = None) -> bool:
    """Atomically reserve one labeling slot for today.

    Seeds today's key with DB count if missing (SET NX).
    Fails closed (returns False) if Redis unavailable.
    """
    key = _get_today_key(now_utc)
    r = redis_client
    try:
        if r is None:
            r = get_budget_redis_client()
        # Seed value computed from DB if key might be unset
        seed = 0
        if r.get(key) is None and db is not None:
            seed = count_labeled_today_db(db, now_utc=now_utc)
            r.set(key, seed, nx=True, ex=KEY_TTL_SECONDS)
        val = r.incr(key)
        if val == 1:
            r.expire(key, KEY_TTL_SECONDS)
        if val > cap:
            r.decr(key)
            return False
        return True
    except Exception as exc:
        logger.warning("gallery_label_budget.reserve: Redis error, failing closed: %s", exc)
        return False


def release(redis_client=None, now_utc: datetime | None = None) -> None:
    """Release a reserved slot (e.g. skipped image after reservation)."""
    key = _get_today_key(now_utc)
    r = redis_client
    try:
        if r is None:
            r = get_budget_redis_client()
        r.decr(key)
    except Exception as exc:
        logger.warning("gallery_label_budget.release: Redis error: %s", exc)
