"""Engine router for AI visual generation (owner plan 2026-10-17).

Selects between ChatGPT (gpt serve via gptcli) and Codex (via 9Router).
Night: Codex only, slow pace, or defer until day_start / Codex reset.
Day: ChatGPT primary, Codex share + overflow up to budget limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone
import logging
import random
from typing import Any
from zoneinfo import ZoneInfo

import redis
from sqlalchemy import or_

from app.config import get_settings
from app.models.ai_copy_events import AICopyEvent
from app.models.settings import Settings
from app.services.codex_budget import codex_resting, get_codex_usage
from app.services import gptcli_client
from app.services.gptcli_client import (
    get_gptcli_config,
    gptcli_applies,
    gptcli_unavailable_until,
)

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
SHARE_REASONS = ("share", "overflow")
CHOICE_REASONS = ("share", "overflow", "gptcli_first")
GPTCLI_QUEUE_LEN_CACHE_KEY = "gptcli:queue_len_cache"
GPTCLI_QUEUE_LEN_TTL_S = 30


@dataclass(frozen=True)
class EnginePlan:
    order: tuple[str, ...]          # each "gptcli" or "9router", tried in this order
    reason: str                     # gptcli_off | night | share | overflow | gptcli_first | gptcli_unavailable | no_engine
    defer_until: datetime | None = None   # aware UTC; only at night when no engine may run


def parse_hhmm(value: str | None, default: str = "05:30") -> tuple[int, int]:
    for candidate in (value, default, "05:30"):
        if candidate and isinstance(candidate, str) and ":" in candidate:
            try:
                parts = candidate.strip().split(":", 1)
                h, m = int(parts[0]), int(parts[1])
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return h, m
            except (ValueError, TypeError):
                pass
    return 5, 30


def is_night(now: datetime, day_start: str, night_start: str = "00:00") -> bool:
    ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    t = ref_now.astimezone(WIB).time()
    ns = time(*parse_hhmm(night_start, "00:00"))
    ds = time(*parse_hhmm(day_start))
    if ns < ds:
        return ns <= t < ds
    if ns > ds:
        return t >= ns or t < ds
    return False


def today_day_start(now: datetime, day_start: str) -> datetime:
    ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    wib_dt = ref_now.astimezone(WIB)
    h, m = parse_hhmm(day_start)
    return datetime(wib_dt.year, wib_dt.month, wib_dt.day, h, m, tzinfo=WIB).astimezone(timezone.utc)


def codex_daily_used(db, now: datetime) -> int:
    if db is None:
        return 0
    ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    wib_dt = ref_now.astimezone(WIB)
    wib_midnight = datetime(wib_dt.year, wib_dt.month, wib_dt.day, 0, 0, 0, tzinfo=WIB)
    utc_start_naive = wib_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    try:
        return (
            db.query(AICopyEvent)
            .filter(
                AICopyEvent.context == "image_gen",
                AICopyEvent.final_provider == "9router",
                AICopyEvent.created_at >= utc_start_naive,
            )
            .count()
        )
    except Exception as exc:
        logger.warning("codex_daily_used error: %s", exc)
        return 0


def _plan_is(message: str | None, reasons: tuple[str, ...]) -> bool:
    """True when an event's text starts with one of the plan markers.

    visual_engine writes "plan=<reason>" alone when it has nothing else to add,
    or "plan=<reason>; ..." otherwise, so both shapes must count."""
    if not message:
        return False
    return any(message == f"plan={r}" or message.startswith(f"plan={r};") for r in reasons)


def codex_share(db, window_jobs: int) -> float:
    if db is None or window_jobs <= 0:
        return 0.0
    try:
        filters = []
        for r in CHOICE_REASONS:
            filters.append(AICopyEvent.error_message == f"plan={r}")
            filters.append(AICopyEvent.error_message.like(f"plan={r};%"))
        events = (
            db.query(AICopyEvent)
            .filter(
                AICopyEvent.context == "image_gen",
                or_(*filters),
            )
            .order_by(AICopyEvent.id.desc())
            .limit(window_jobs)
            .all()
        )
        if not events:
            return 0.0
        match_count = sum(
            1 for ev in events
            if _plan_is(ev.error_message, SHARE_REASONS) and ev.final_provider == "9router"
        )
        return match_count / len(events)
    except Exception as exc:
        logger.warning("codex_share error: %s", exc)
        return 0.0


def visual_queue_len() -> int:
    try:
        r = redis.from_url(get_settings().redis_url)
        return int(r.llen("visual"))
    except Exception as exc:
        logger.warning("engine_router: failed to get visual queue length: %s", exc)
        return 0


def _redis():
    return redis.from_url(get_settings().redis_url, decode_responses=True)


def gptcli_queue_len(db) -> int:
    try:
        r = _redis()
        cached = r.get(GPTCLI_QUEUE_LEN_CACHE_KEY)
        if cached is not None:
            return int(cached)

        cfg = gptcli_client.get_gptcli_config(db)
        health = gptcli_client.serve_health(cfg)
        queue_len = 0
        if isinstance(health, dict):
            queue_len = max(0, int(health.get("queue_len") or 0))
            profiles = health.get("profiles")
            if isinstance(profiles, dict) and any(
                isinstance(p, dict) and p.get("running") is True for p in profiles.values()
            ):
                queue_len += 1

        # Cache misses too, so an unreachable gpt serve costs one 10 s probe per TTL.

        try:
            r.set(GPTCLI_QUEUE_LEN_CACHE_KEY, str(queue_len), ex=GPTCLI_QUEUE_LEN_TTL_S)
        except Exception as exc:
            logger.warning("engine_router: failed to cache gptcli queue len: %s", exc)

        return queue_len
    except Exception as exc:
        logger.warning("engine_router: gptcli_queue_len error: %s", exc)
        return 0


def codex_allowed(db, now: datetime, *, night: bool, settings=None) -> tuple[bool, str]:
    if codex_resting(db, now):
        return False, "codex_resting"

    if settings is None and db is not None:
        try:
            settings = db.query(Settings).first()
        except Exception:
            settings = None

    night_limit = getattr(settings, "codex_night_max_pct", 70) if settings is not None else 70
    if night_limit is None:
        night_limit = 70
    fallback_limit = getattr(settings, "codex_fallback_max_pct", 85) if settings is not None else 85
    if fallback_limit is None:
        fallback_limit = 85
    daily_max = getattr(settings, "visual_engine_daily_max", 60) if settings is not None else 60
    if daily_max is None:
        daily_max = 60

    usage = get_codex_usage(db, now)
    limit = night_limit if night else fallback_limit
    if usage.window_pct >= limit:
        return False, f"codex_pct {usage.window_pct:.0f}>={limit}"

    if codex_daily_used(db, now) >= daily_max:
        return False, "codex_daily_cap"

    return True, "ok"


def plan_engines(
    db,
    fanpage_id: int | None,
    now: datetime,
    *,
    exclude: frozenset = frozenset(),
    rand=random.random,
) -> EnginePlan:
    cfg = get_gptcli_config(db)
    if not gptcli_applies(cfg, fanpage_id):
        return EnginePlan(("9router",), "gptcli_off")

    settings = db.query(Settings).first() if db is not None else None
    day_start = getattr(settings, "ai_day_start", "05:30") or "05:30"
    night_start = getattr(settings, "ai_night_start", "00:00") or "00:00"

    if is_night(now, day_start, night_start):
        ok, _why = codex_allowed(db, now, night=True, settings=settings)
        if ok and "9router" not in exclude:
            return EnginePlan(("9router",), "night")
        usage = get_codex_usage(db, now)
        day_start_dt = today_day_start(now, day_start)
        ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        ref_now_utc = ref_now.astimezone(timezone.utc)
        if usage.resets_at is not None:
            resets_utc = (
                usage.resets_at
                if usage.resets_at.tzinfo is not None
                else usage.resets_at.replace(tzinfo=timezone.utc)
            ).astimezone(timezone.utc)
            if resets_utc > ref_now_utc:
                defer_until = min(day_start_dt, resets_utc)
            else:
                defer_until = day_start_dt
        else:
            defer_until = day_start_dt
        return EnginePlan((), "night", defer_until=defer_until)

    if gptcli_unavailable_until(now) is not None:
        order: tuple[str, ...] = ("9router",)
        reason = "gptcli_unavailable"
    else:
        window_jobs = getattr(settings, "codex_share_window_jobs", 40) or 40
        share_percent = getattr(settings, "codex_share_percent", 35)
        if share_percent is None:
            share_percent = 35
        share = codex_share(db, window_jobs)
        left = max(0.0, (share_percent / 100.0) - share)
        usage = get_codex_usage(db, now)

        ref_now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
        wib = ref_now.astimezone(WIB)
        if usage.weekly_pct is not None and usage.weekly_pct > (wib.weekday() + 1) / 7.0 * 100.0 + 15.0:
            left = 0.0

        share_max_pct = getattr(settings, "codex_share_max_pct", 60)
        if share_max_pct is None:
            share_max_pct = 60
        overflow_queue_len = getattr(settings, "codex_overflow_queue_len", 3)
        if overflow_queue_len is None:
            overflow_queue_len = 3

        if left > 0.0 and usage.window_pct < share_max_pct and not codex_resting(db, now):
            if visual_queue_len() >= overflow_queue_len or gptcli_queue_len(db) >= overflow_queue_len:
                order = ("9router", "gptcli")
                reason = "overflow"
            elif rand() < left:
                order = ("9router", "gptcli")
                reason = "share"
            else:
                order = ("gptcli", "9router")
                reason = "gptcli_first"
        else:
            order = ("gptcli", "9router")
            reason = "gptcli_first"

    order = tuple(eng for eng in order if eng not in exclude)

    if "9router" in order:
        allowed, _why = codex_allowed(db, now, night=False, settings=settings)
        if not allowed:
            order = tuple(eng for eng in order if eng != "9router")

    if not order:
        return EnginePlan((), "no_engine")

    return EnginePlan(order, reason)


def render_queue_for(db, fanpage, content_kind: str, visual_engine_override: str | None = None) -> str | None:
    try:
        if fanpage is None:
            return None
        fanpage_id = getattr(fanpage, "id", None)
        cfg = get_gptcli_config(db)
        if not gptcli_applies(cfg, fanpage_id):
            return None

        if content_kind == "news":
            toggle = getattr(fanpage, "news_visual_engine", None)
        elif content_kind == "facebook":
            toggle = getattr(fanpage, "fb_visual_engine", None)
        elif content_kind == "instagram":
            toggle = visual_engine_override if visual_engine_override is not None else getattr(fanpage, "visual_engine", None)
        else:
            return None

        if toggle == "chatgpt":
            return "visual"
        return None
    except Exception as exc:
        logger.warning("render_queue_for error: %s", exc)
        return None
