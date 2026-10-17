"""Codex (9Router cx/*) budget (owner plan 2026-10-17): percent of the 5-hour window, rest after 429 / >= rest pct,
and the slow night pace. Accuracy over availability: an unknown usage never counts as 0 %."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import httpx
import redis

from app.config import get_settings
from app.models.ai_copy_events import AICopyEvent
from app.models.settings import Settings
from app.services.nine_router import get_nine_router_config

logger = logging.getLogger(__name__)

WINDOW_HOURS = 5
REST_KEY = "codex:rest_until"     # epoch seconds string
LAST_AT_KEY = "codex:last_at"     # epoch seconds string (last Codex image call)
USAGE_CACHE_KEY = "codex:usage_cache"   # JSON, ttl 60 s (URL source only)
URL_TIMEOUT_S = 10


@dataclass(frozen=True)
class CodexUsage:
    window_pct: float                 # 0-100
    weekly_pct: float | None
    resets_at: datetime | None        # aware UTC
    source: Literal["9router", "estimate"]


def _settings(db) -> Settings | None:
    if db is None:
        return None
    try:
        return db.query(Settings).first()
    except Exception as exc:
        logger.warning("codex_budget: failed reading settings: %s", exc)
        return None


def _redis():
    try:
        settings = get_settings()
        return redis.from_url(settings.redis_url, decode_responses=True)
    except Exception as exc:
        logger.warning("codex_budget: Redis connection failed: %s", exc)
        return None


def _find_rate_limit_dict(payload: dict) -> dict | None:
    if not isinstance(payload, dict):
        return None
    candidates: list[dict] = []
    for k in ("rate_limit", "rate_limits"):
        val = payload.get(k)
        if isinstance(val, dict):
            candidates.append(val)
    for v in payload.values():
        if isinstance(v, dict):
            for k in ("rate_limit", "rate_limits"):
                val = v.get(k)
                if isinstance(val, dict):
                    candidates.append(val)
    for cand in candidates:
        if "primary_window" in cand and isinstance(cand["primary_window"], dict):
            return cand
    if candidates:
        return candidates[0]
    return None


def parse_usage_payload(payload: dict, now: datetime | None = None) -> CodexUsage | None:
    target = _find_rate_limit_dict(payload)
    if target is None:
        return None

    pw = target.get("primary_window")
    if not isinstance(pw, dict):
        return None

    used_percent = pw.get("used_percent")
    if used_percent is None or isinstance(used_percent, bool):
        return None
    try:
        window_pct = float(used_percent)
    except (ValueError, TypeError):
        return None

    window_pct = max(0.0, min(100.0, window_pct))

    resets_at = None
    if "reset_at" in pw and pw["reset_at"] is not None and not isinstance(pw["reset_at"], bool):
        try:
            resets_at = datetime.fromtimestamp(float(pw["reset_at"]), tz=timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            return None
    elif "reset_after_seconds" in pw and pw["reset_after_seconds"] is not None and not isinstance(pw["reset_after_seconds"], bool):
        try:
            secs = float(pw["reset_after_seconds"])
            base_now = now or datetime.now(timezone.utc)
            if base_now.tzinfo is None:
                base_now = base_now.replace(tzinfo=timezone.utc)
            resets_at = base_now + timedelta(seconds=secs)
        except (ValueError, TypeError, OverflowError):
            return None

    weekly_pct = None
    sw = target.get("secondary_window")
    if isinstance(sw, dict):
        sw_pct = sw.get("used_percent")
        if sw_pct is not None and not isinstance(sw_pct, bool):
            try:
                weekly_pct = max(0.0, min(100.0, float(sw_pct)))
            except (ValueError, TypeError):
                return None

    return CodexUsage(
        window_pct=window_pct,
        weekly_pct=weekly_pct,
        resets_at=resets_at,
        source="9router",
    )


def estimate_usage(db, now: datetime) -> CodexUsage:
    if now.tzinfo is not None:
        utc_now = now.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        utc_now = now
    cutoff = utc_now - timedelta(hours=WINDOW_HOURS)

    n = 0
    unknown = db is None
    if db is not None:
        try:
            n = (
                db.query(AICopyEvent)
                .filter(
                    AICopyEvent.context == "image_gen",
                    AICopyEvent.final_provider == "9router",
                    AICopyEvent.created_at > cutoff,
                )
                .count()
            )
        except Exception as exc:
            logger.warning("codex_budget: failed to query AICopyEvent: %s", exc)
            unknown = True

    row = _settings(db)
    raw_cap = getattr(row, "codex_window_capacity_est", 60)
    try:
        cap_val = int(raw_cap) if raw_cap is not None else 60
    except (ValueError, TypeError):
        cap_val = 60
    capacity = max(1, cap_val)

    # Unknown usage never counts as 0 % (owner rule: accuracy over availability) — treat it as full.
    window_pct = 100.0 if unknown else min(100.0, max(0.0, (n / capacity) * 100.0))
    return CodexUsage(
        window_pct=float(window_pct),
        weekly_pct=None,
        resets_at=None,
        source="estimate",
    )


def get_codex_usage(db, now: datetime) -> CodexUsage:
    # The real numbers come first: the VPS timer /opt/codex-usage/sync.sh (every 25-40 min) reads them from the
    # local 9Router quota endpoint and caches them here for 45 min. Without a fresh cache: URL, then the estimate.
    row = _settings(db)
    raw_url = getattr(row, "codex_usage_url", None)
    url = str(raw_url).strip() if raw_url else ""

    r = None
    try:
        r = _redis()
    except Exception as exc:
        logger.warning("codex_budget: redis error connecting: %s", exc)

    if r is not None:
        try:
            cached = r.get(USAGE_CACHE_KEY)
            if cached:
                data = json.loads(cached)
                resets_at = None
                if data.get("resets_at"):
                    resets_at = datetime.fromisoformat(data["resets_at"])
                    if resets_at.tzinfo is None:
                        resets_at = resets_at.replace(tzinfo=timezone.utc)
                return CodexUsage(
                    window_pct=float(data["window_pct"]),
                    weekly_pct=float(data["weekly_pct"]) if data.get("weekly_pct") is not None else None,
                    resets_at=resets_at,
                    source=data.get("source", "9router"),
                )
        except Exception as exc:
            logger.warning("codex_budget: cache read error: %s", exc)

    if not url:
        return estimate_usage(db, now)

    api_key = ""
    try:
        cfg = get_nine_router_config()
        api_key = cfg.api_key
    except Exception as exc:
        logger.warning("codex_budget: failed to get 9Router config: %s", exc)

    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        resp = httpx.get(url, headers=headers, timeout=URL_TIMEOUT_S)
        if resp.status_code != 200:
            logger.warning("codex_budget: usage endpoint returned status %s", resp.status_code)
            return estimate_usage(db, now)
        payload = resp.json()
    except Exception as exc:
        logger.warning("codex_budget: usage fetch failed: %s", exc)
        return estimate_usage(db, now)

    usage = parse_usage_payload(payload, now=now)
    if usage is None:
        logger.warning("codex_budget: invalid usage payload format")
        return estimate_usage(db, now)

    if r is not None:
        try:
            cache_data = {
                "window_pct": usage.window_pct,
                "weekly_pct": usage.weekly_pct,
                "resets_at": usage.resets_at.isoformat() if usage.resets_at else None,
                "source": usage.source,
            }
            r.set(USAGE_CACHE_KEY, json.dumps(cache_data), ex=60)
        except Exception as exc:
            logger.warning("codex_budget: cache write error: %s", exc)

    return usage


def mark_codex_rest(until: datetime) -> None:
    try:
        r = _redis()
        if r is None:
            return
        aware_until = until if until.tzinfo is not None else until.replace(tzinfo=timezone.utc)
        epoch = aware_until.timestamp()
        diff = epoch - datetime.now(timezone.utc).timestamp()
        ttl = max(1, int(diff) + 60)
        r.set(REST_KEY, str(epoch), ex=ttl)
    except Exception as exc:
        logger.warning("codex_budget: failed to mark rest: %s", exc)


def codex_resting(db, now: datetime) -> bool:
    now_epoch = now.timestamp() if now.tzinfo is not None else now.replace(tzinfo=timezone.utc).timestamp()
    try:
        r = _redis()
        if r is not None:
            val = r.get(REST_KEY)
            if val is not None:
                try:
                    if float(val) > now_epoch:
                        return True
                except (ValueError, TypeError):
                    pass
    except Exception as exc:
        logger.warning("codex_budget: redis error in codex_resting: %s", exc)

    usage = get_codex_usage(db, now)
    row = _settings(db)
    raw_rest_pct = getattr(row, "codex_rest_pct", 95)
    try:
        rest_pct = float(raw_rest_pct) if raw_rest_pct is not None else 95.0
    except (ValueError, TypeError):
        rest_pct = 95.0

    if usage.window_pct >= rest_pct:
        rest_until = usage.resets_at or (now + timedelta(hours=1))
        mark_codex_rest(rest_until)
        return True
    return False


def mark_codex_used(now: datetime) -> None:
    try:
        r = _redis()
        if r is None:
            return
        now_epoch = now.timestamp() if now.tzinfo is not None else now.replace(tzinfo=timezone.utc).timestamp()
        r.set(LAST_AT_KEY, str(now_epoch), ex=86400)
    except Exception as exc:
        logger.warning("codex_budget: failed to mark used: %s", exc)


def wait_night_gap(
    db,
    now: datetime,
    *,
    sleep: Any = time.sleep,
    rand: Any = random.uniform,
) -> float:
    try:
        r = _redis()
        if r is None:
            return 0.0
        val = r.get(LAST_AT_KEY)
    except Exception as exc:
        logger.warning("codex_budget: redis error reading last_at: %s", exc)
        return 0.0

    if val is None:
        return 0.0

    try:
        last = float(val)
    except (ValueError, TypeError):
        return 0.0

    row = _settings(db)
    raw_min_gap = getattr(row, "codex_night_min_gap_s", 240)
    raw_jitter = getattr(row, "codex_night_gap_jitter_s", 60)
    try:
        min_gap = float(raw_min_gap) if raw_min_gap is not None else 240.0
    except (ValueError, TypeError):
        min_gap = 240.0
    try:
        jitter = float(raw_jitter) if raw_jitter is not None else 60.0
    except (ValueError, TypeError):
        jitter = 60.0

    gap = max(0.0, min_gap + rand(-jitter, jitter))
    now_epoch = now.timestamp() if now.tzinfo is not None else now.replace(tzinfo=timezone.utc).timestamp()
    remaining = last + gap - now_epoch

    if remaining > 0:
        slept = min(remaining, min_gap + jitter)
        sleep(slept)
        return float(slept)
    return 0.0
