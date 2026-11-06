"""Pause all 9Router vision calls for a while after repeated failures (owner 2026-11-07: retry storms during
outages multiplied traffic and helped get accounts disabled). Redis-backed so every worker shares it.
Never blocks when Redis is down.
"""

import functools
import logging

logger = logging.getLogger(__name__)

FAIL_THRESHOLD = 5
FAIL_WINDOW_SECONDS = 300
PAUSE_SECONDS = 600
OPEN_KEY = "vision_breaker:open"
FAILS_KEY = "vision_breaker:fails"


class VisionPausedError(RuntimeError):
    pass


@functools.lru_cache(maxsize=1)
def _redis():
    # One shared client per process; every _vision_chat call checks the breaker.
    import redis
    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True)


def is_open(*, redis_client=None) -> bool:
    try:
        r = redis_client if redis_client is not None else _redis()
        return bool(r.exists(OPEN_KEY))
    except Exception as exc:
        logger.debug("vision breaker check failed: %s", exc)
        return False


def record_failure(*, redis_client=None) -> None:
    try:
        r = redis_client if redis_client is not None else _redis()
        n = r.incr(FAILS_KEY)
        if n == 1:
            r.expire(FAILS_KEY, FAIL_WINDOW_SECONDS)
        if n >= FAIL_THRESHOLD:
            r.set(OPEN_KEY, 1, ex=PAUSE_SECONDS)
            r.delete(FAILS_KEY)
            logger.warning("vision breaker: %d failed vision calls, pausing vision for %d s", n, PAUSE_SECONDS)
    except Exception as exc:
        logger.debug("vision breaker record_failure failed: %s", exc)


def record_success(*, redis_client=None) -> None:
    try:
        r = redis_client if redis_client is not None else _redis()
        r.delete(FAILS_KEY)
    except Exception as exc:
        logger.debug("vision breaker record_success failed: %s", exc)
