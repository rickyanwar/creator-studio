import random
import re
from datetime import date, datetime, time, timedelta


def effective_daily_cap(
    start_per_day: int, weekly_increase: int, max_per_day: int, enabled_since: date, today: date,
) -> int:
    weeks = max(0, (today - enabled_since).days // 7)
    return max(0, min(max_per_day, start_per_day + weekly_increase * weeks))


def in_task_hours(now_local: datetime, start: str, end: str) -> bool:
    boundaries = []
    for value in (start, end):
        if not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
            raise ValueError("Task hours must use HH:MM format")
        hour, minute = map(int, value.split(":"))
        boundaries.append(time(hour, minute))
    start_time, end_time = boundaries
    current = now_local.time()
    if end_time <= start_time:
        return current >= start_time or current < end_time
    return start_time <= current < end_time


def next_slot(
    last_sent_at: datetime | None, now: datetime, min_gap_minutes: int, rng: random.Random,
) -> datetime:
    if last_sent_at is None:
        return now
    gap = timedelta(minutes=rng.uniform(min_gap_minutes, min_gap_minutes * 3))
    return max(now, last_sent_at + gap)


def remaining_today(cap: int, sent_today: int) -> int:
    return max(0, cap - sent_today)
