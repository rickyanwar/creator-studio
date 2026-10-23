"""Pure scheduling logic for gemini-web-serve. No I/O, no gemini import."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone, tzinfo


# Asia/Jakarta = UTC+7, no DST
_JAKARTA_OFFSET = timezone(timedelta(hours=7))


def _tz(name: str) -> tzinfo:
    """Minimal fixed-offset TZ resolver. Only Asia/Jakarta supported."""
    if name == "Asia/Jakarta":
        return _JAKARTA_OFFSET
    raise ValueError(f"Unsupported tz: {name}")


@dataclass
class SchedulerConfig:
    sleep_min_h: float = 3.5
    sleep_max_h: float = 4.5
    jitter_min: float = 45.0  # minutes
    breaks_min: int = 1
    breaks_max: int = 2
    break_min_m: float = 20.0
    break_max_m: float = 60.0
    gap_min_m: float = 4.0
    gap_max_m: float = 9.0
    cap_min: int = 30
    cap_max: int = 40
    tz: str = "Asia/Jakarta"


@dataclass
class DayPlan:
    cap_today: int = 0
    sleep_start: float = 0.0  # epoch
    sleep_end: float = 0.0
    breaks: list[list[float]] = field(default_factory=list)  # [[start, end], ...]


def _local_midnight_epoch(day: date, tz_offset: tzinfo) -> float:
    """Epoch seconds for midnight of `day` in the given timezone."""
    dt = datetime(day.year, day.month, day.day, tzinfo=tz_offset)
    return dt.timestamp()


def _local_day(now: float, tz_offset: tzinfo) -> date:
    return datetime.fromtimestamp(now, tz=tz_offset).date()


def _compute_sleep_windows(
    names: list[str], midnight: float, cfg: SchedulerConfig, rng: random.Random
) -> list[tuple[float, float]]:
    """Compute staggered sleep windows with no overlap."""
    n = len(names)
    sleep_windows: list[tuple[float, float]] = []
    for i in range(n):
        anchor = midnight + i * (86400.0 / n) + 3600.0  # +1h
        jitter = rng.uniform(-cfg.jitter_min, cfg.jitter_min) * 60.0
        duration = rng.uniform(cfg.sleep_min_h, cfg.sleep_max_h) * 3600.0
        start = anchor + jitter
        if sleep_windows and start < sleep_windows[-1][1]:
            start = sleep_windows[-1][1]
        end = start + duration
        sleep_windows.append((start, end))
    return sleep_windows


def _free_slots(
    blocked: list[tuple[float, float]], day_start: float, day_end: float, min_dur: float
) -> list[tuple[float, float]]:
    """Find free intervals in [day_start, day_end] not overlapping blocked."""
    edges = sorted(blocked, key=lambda x: x[0])
    slots = []
    cursor = day_start
    for bs, be in edges:
        if cursor < bs and (bs - cursor) >= min_dur:
            slots.append((cursor, bs))
        cursor = max(cursor, be)
    if cursor < day_end and (day_end - cursor) >= min_dur:
        slots.append((cursor, day_end))
    return slots


def _place_account_breaks(
    name: str,
    plan: DayPlan,
    all_sleeps: list[tuple[float, float]],
    all_breaks: list[tuple[float, float]],
    day_start: float,
    day_end: float,
    cfg: SchedulerConfig,
    rng: random.Random,
) -> None:
    """Place 1-2 random non-overlapping breaks for an account."""
    n_breaks = rng.randint(cfg.breaks_min, cfg.breaks_max)
    min_dur = cfg.break_min_m * 60.0
    max_dur = cfg.break_max_m * 60.0
    placed = 0
    attempts = 0
    while placed < n_breaks and attempts < 200:
        attempts += 1
        blocked = all_sleeps + all_breaks
        slots = _free_slots(blocked, day_start, day_end, min_dur)
        if not slots:
            break
        weights = [max(0.0, s[1] - s[0] - min_dur) for s in slots]
        slot = rng.choice(slots) if sum(weights) <= 0 else rng.choices(slots, weights=weights, k=1)[0]
        slot_len = slot[1] - slot[0]
        dur = rng.uniform(min_dur, min(max_dur, slot_len))
        b_start = rng.uniform(slot[0], slot[1] - dur)
        b_end = b_start + dur
        if any(_overlaps(b_start, b_end, s, e) for s, e in blocked):
            continue
        plan.breaks.append([b_start, b_end])
        all_breaks.append((b_start, b_end))
        placed += 1


def plan_day(
    names_active: list[str] | dict[str, dict],
    day: date,
    cfg: SchedulerConfig,
    rng: random.Random,
) -> dict[str, DayPlan]:
    """Compute sleep windows, breaks, and caps for all active accounts."""
    if isinstance(names_active, dict):
        names = sorted([
            n for n, st in names_active.items()
            if st.get("status") not in ("needs_login", "no_proxy", "needs_tos")
        ])
    else:
        names = sorted(names_active)
    if not names:
        return {}

    tz_off = _tz(cfg.tz)
    midnight = _local_midnight_epoch(day, tz_off)
    sleep_windows = _compute_sleep_windows(names, midnight, cfg, rng)

    plans: dict[str, DayPlan] = {}
    for i, name in enumerate(names):
        s_start, s_end = sleep_windows[i]
        plans[name] = DayPlan(
            cap_today=rng.randint(cfg.cap_min, cfg.cap_max),
            sleep_start=s_start,
            sleep_end=s_end,
        )

    all_sleeps = sleep_windows[:]
    all_breaks: list[tuple[float, float]] = []
    day_start = midnight
    day_end = midnight + 86400.0

    for name in names:
        _place_account_breaks(
            name, plans[name], all_sleeps, all_breaks, day_start, day_end, cfg, rng
        )

    return plans


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return a_start < b_end and b_start < a_end


def is_available(state: dict, now: float) -> tuple[bool, str]:
    """Check if an account is available. Returns (available, reason)."""
    status = state.get("status", "ok")

    if status == "needs_login":
        return False, "needs_login"
    if status == "no_proxy":
        return False, "no_proxy"
    if status == "needs_tos":
        cu = state.get("cooldown_until")
        if cu is not None and now < cu:
            return False, "needs_tos"
        if cu is not None and now >= cu:
            state["status"] = "ok"
            state["cooldown_until"] = None
            status = "ok"
        else:
            return False, "needs_tos"
    if status == "abuse_flag":
        cu = state.get("cooldown_until")
        if cu is not None and now < cu:
            return False, "abuse_flag"
        state["status"] = "ok"
        state["cooldown_until"] = None
        status = "ok"
    if status == "cooldown":
        cu = state.get("cooldown_until")
        if cu is not None and now < cu:
            return False, "cooldown"
        state["status"] = "ok"
        state["cooldown_until"] = None
        status = "ok"
    if status == "quota_low":
        cu = state.get("cooldown_until")
        if cu is not None and now < cu:
            return False, "quota_low"
        state["status"] = "ok"
        state["cooldown_until"] = None
        status = "ok"

    # Sleep window
    ss = state.get("sleep_start", 0.0)
    se = state.get("sleep_end", 0.0)
    if ss and se and ss <= now < se:
        return False, "sleeping"

    # Breaks
    for brk in state.get("breaks", []):
        if len(brk) >= 2 and brk[0] <= now < brk[1]:
            return False, "on_break"

    # Gap
    noa = state.get("next_ok_at")
    if noa is not None and now < noa:
        return False, "gap"

    # Daily cap
    used = state.get("used_today", 0)
    cap = state.get("cap_today", 0)
    if cap > 0 and used >= cap:
        return False, "daily_cap"

    return True, "ok"


def pick_account(
    states: dict[str, dict], now: float, rng: random.Random
) -> str | None:
    """Pick an available account, preferring higher quota_min_fraction then least recently used."""
    available = []
    for name, st in states.items():
        ok, _ = is_available(st, now)
        if ok:
            available.append(name)
    if not available:
        return None
    # Sort by:
    # 1. quota_min_fraction descending (None counts as 1.0 — highest)
    # 2. last_used_at ascending (None first = never used)
    # 3. name for stable tie-break
    available.sort(key=lambda n: (
        -(states[n].get("quota_min_fraction") if states[n].get("quota_min_fraction") is not None else 1.0),
        states[n].get("last_used_at") is not None,
        states[n].get("last_used_at") or 0.0,
        n,
    ))
    if len(available) >= 2:
        return rng.choice(available[:2])
    return available[0]


def next_ready_at(states: dict[str, dict], now: float, tz_name: str = "Asia/Jakarta") -> float | None:
    """Earliest time any non-permanently-blocked account becomes available."""
    candidates: list[float] = []
    tz_off = _tz(tz_name)
    for name, st in states.items():
        status = st.get("status", "ok")
        if status in ("needs_login", "no_proxy"):
            continue
        ok, reason = is_available(st, now)
        if ok:
            return now  # at least one is ready now
        if reason == "sleeping":
            candidates.append(st.get("sleep_end", now))
        elif reason == "on_break":
            for brk in st.get("breaks", []):
                if len(brk) >= 2 and brk[0] <= now < brk[1]:
                    candidates.append(brk[1])
                    break
        elif reason == "gap":
            noa = st.get("next_ok_at")
            if noa is not None:
                candidates.append(noa)
        elif reason in ("cooldown", "abuse_flag", "needs_tos", "quota_low"):
            cu = st.get("cooldown_until")
            if cu is not None:
                candidates.append(cu)
        elif reason == "daily_cap":
            # Next local midnight
            local_day = _local_day(now, tz_off)
            next_mid = _local_midnight_epoch(
                local_day + timedelta(days=1), tz_off
            )
            candidates.append(next_mid)

    valid = [c for c in candidates if c is not None and c > now]
    return min(valid) if valid else None


def after_use(state: dict, now: float, cfg: SchedulerConfig, rng: random.Random) -> None:
    """Update state after a successful use."""
    state["last_used_at"] = now
    state["used_today"] = state.get("used_today", 0) + 1
    gap = rng.uniform(cfg.gap_min_m, cfg.gap_max_m) * 60.0
    state["next_ok_at"] = now + gap
    state["cooldown_level"] = 0
    state["status"] = "ok"


_COOLDOWN_DURATIONS = {
    1: 30 * 60,      # 30 min
    2: 2 * 3600,     # 2 h
    3: 12 * 3600,    # 12 h
}


def apply_error(state: dict, error_type: str, now: float) -> None:
    """Update state after an error."""
    state["last_error_type"] = error_type

    if error_type == "auth":
        state["status"] = "needs_login"
        return

    if error_type == "tos":
        state["status"] = "needs_tos"
        state["cooldown_until"] = now + 6 * 3600
        return

    if error_type in ("usage_limit", "blocked"):
        level = min(state.get("cooldown_level", 0) + 1, 3)
        state["cooldown_level"] = level
        state["cooldown_until"] = now + _COOLDOWN_DURATIONS[level]
        state["status"] = "cooldown"
        return

    if error_type == "abuse":
        state["status"] = "abuse_flag"
        state["cooldown_until"] = now + 24 * 3600
        return

    if error_type in ("timeout", "network"):
        state["next_ok_at"] = now + 10 * 60
        return

    if error_type == "quota_low":
        # Do NOT raise cooldown_level. Just set cooldown_until from quota_reset_at.
        reset_at = state.get("quota_reset_at")
        if reset_at is not None and reset_at > now:
            cooldown_until = reset_at + state.get("_quota_jitter", 0.0)
        else:
            cooldown_until = now + 3600.0
        state["cooldown_until"] = cooldown_until
        state["status"] = "quota_low"
        return
