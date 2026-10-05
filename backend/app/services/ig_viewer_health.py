"""Best-effort Redis health counters for Instagram viewer tiers."""

import logging
from datetime import datetime, timezone

from app.services.ig_viewer_sanitize import sanitize_error

logger = logging.getLogger(__name__)

TIERS = ("gramsnap", "anonyig", "igstoryviewer")
ERROR_KINDS = ("site_down", "blocked", "script")
ALERT_MIN_CONSECUTIVE = 5
ALERT_MIN_USERS = 2
ALERT_MIN_STREAK_MINUTES = 60
HEALTH_TTL_SECONDS = 14 * 24 * 60 * 60
REDIS_TIMEOUT_SECONDS = 2


class HealthUnavailableError(Exception):
    """Redis health snapshot could not be read."""


_UPDATE_LUA = """
if ARGV[1] == 'success' then
    redis.call('HSET', KEYS[1], 'last_success_at', ARGV[2],
               'consecutive_failures', 0, 'streak_started_at', '')
    redis.call('DEL', KEYS[2])
else
    redis.call('HINCRBY', KEYS[1], 'consecutive_failures', 1)
    if redis.call('HGET', KEYS[1], 'streak_started_at') == false or
       redis.call('HGET', KEYS[1], 'streak_started_at') == '' then
        redis.call('HSET', KEYS[1], 'streak_started_at', ARGV[2])
    end
    redis.call('HSET', KEYS[1], 'last_failure_at', ARGV[2],
               'last_error', ARGV[4], 'last_error_kind', ARGV[5])
    redis.call('SADD', KEYS[2], ARGV[3])
end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[6]))
if redis.call('EXISTS', KEYS[2]) == 1 then
    redis.call('EXPIRE', KEYS[2], tonumber(ARGV[6]))
end
"""


def _now(now):
    """Naive injected datetimes are UTC, never host-local."""
    value = now if now is not None else datetime.now(timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _redis(redis_client):
    if redis_client is not None:
        return redis_client
    import redis
    from app.config import get_settings

    return redis.from_url(get_settings().redis_url, decode_responses=True,
                          socket_connect_timeout=REDIS_TIMEOUT_SECONDS,
                          socket_timeout=REDIS_TIMEOUT_SECONDS)


def _text(value):
    return value.decode() if isinstance(value, bytes) else value


def is_unhealthy(tier_state: dict, now) -> bool:
    """Alert only for sustained script/blocked failures across accounts."""
    started = tier_state.get("streak_started_at")
    if not started or tier_state.get("last_error_kind") not in ("script", "blocked"):
        return False
    try:
        return (int(tier_state.get("consecutive_failures", 0)) >= ALERT_MIN_CONSECUTIVE
                and int(tier_state.get("distinct_users", 0)) >= ALERT_MIN_USERS
                and (_now(now) - _now(datetime.fromisoformat(started))).total_seconds()
                >= ALERT_MIN_STREAK_MINUTES * 60)
    except (ValueError, TypeError):
        return False


def record_tier_result(tier: str, username: str, ok: bool,
                       error_kind: str | None = None, error: str | None = None,
                       *, redis_client=None, now=None) -> None:
    if tier not in TIERS or error_kind not in (ERROR_KINDS if not ok else (None, *ERROR_KINDS)):
        logger.warning("IG viewer health ignored invalid tier or error kind")
        return
    key = f"ig_viewer:health:{tier}"
    try:
        r = _redis(redis_client)
        timestamp = _now(now).isoformat()
        r.eval(_UPDATE_LUA, 2, key, f"{key}:users", "success" if ok else "failure",
               timestamp, username, sanitize_error(error or ""), error_kind or "", HEALTH_TTL_SECONDS)
    except Exception as exc:
        logger.warning("IG viewer health Redis write failed for %s: %s", tier, type(exc).__name__)


def get_health(*, redis_client=None, now=None, strict=False) -> dict:
    timestamp = _now(now)
    tiers = {}
    try:
        r = _redis(redis_client)
        with r.pipeline(transaction=True) as pipe:
            for tier in TIERS:
                key = f"ig_viewer:health:{tier}"
                pipe.hgetall(key)
                pipe.scard(f"{key}:users")
            results = pipe.execute()
    except Exception as exc:
        logger.warning("IG viewer health Redis read failed: %s", type(exc).__name__)
        if strict:
            raise HealthUnavailableError("health_unavailable") from exc
        results = [{}, 0] * len(TIERS)
    for index, tier in enumerate(TIERS):
        state = {field: "" for field in ("last_success_at", "last_failure_at",
                                        "streak_started_at", "last_error", "last_error_kind")}
        state.update(consecutive_failures=0, distinct_users=0)
        try:
            state.update({_text(k): _text(v) for k, v in results[index * 2].items()})
            state["consecutive_failures"] = int(state["consecutive_failures"])
            state["distinct_users"] = int(results[index * 2 + 1])
        except Exception as exc:
            logger.warning("IG viewer health Redis read failed for %s: %s", tier, type(exc).__name__)
            if strict:
                raise HealthUnavailableError("health_unavailable") from exc
        state["last_error"] = sanitize_error(state["last_error"])
        state["unhealthy"] = is_unhealthy(state, timestamp)
        tiers[tier] = state
    return {"tiers": tiers, "all_tiers_down": all(
        state["consecutive_failures"] >= 3
        and (state["unhealthy"] or state["last_error_kind"] == "site_down")
        for state in tiers.values()), "generated_at": timestamp.isoformat()}
