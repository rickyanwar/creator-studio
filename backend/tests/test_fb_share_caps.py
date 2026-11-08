import random
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.fb_share.caps import effective_daily_cap, in_task_hours, next_slot, remaining_today


@pytest.mark.parametrize("days,start,increase,maximum,expected", [
    (-7, 1, 2, 10, 1), (0, 1, 2, 10, 1), (6, 1, 2, 10, 1),
    (7, 1, 2, 10, 3), (13, 1, 2, 10, 3), (14, 1, 2, 10, 5),
    (100, 1, 2, 10, 10), (0, -2, 0, 10, 0), (0, 1, 0, -1, 0),
])
def test_effective_cap(days, start, increase, maximum, expected):
    since = date(2026, 1, 1)
    assert effective_daily_cap(start, increase, maximum, since, since + timedelta(days=days)) == expected


@pytest.mark.parametrize("clock,start,end,expected", [
    ("08:59", "09:00", "17:00", False), ("09:00", "09:00", "17:00", True),
    ("16:59", "09:00", "17:00", True), ("17:00", "09:00", "17:00", False),
    ("23:00", "22:00", "06:00", True), ("05:59", "22:00", "06:00", True),
    ("06:00", "22:00", "06:00", False), ("12:00", "22:00", "06:00", False),
    ("12:00", "09:00", "09:00", True),
])
def test_hours(clock, start, end, expected):
    now = datetime.fromisoformat(f"2026-01-01T{clock}:00+07:00")
    assert in_task_hours(now, start, end) is expected


@pytest.mark.parametrize("bad", ["9:00", "09:0", "24:00", "12:60", "12:00:00", " 09:00", "abc", ""])
@pytest.mark.parametrize("side", ["start", "end"])
def test_bad_hours(bad, side):
    args = {"start": "09:00", "end": "17:00", side: bad}
    with pytest.raises(ValueError):
        in_task_hours(datetime(2026, 1, 1), **args)


def test_next_slot_seed_bounds_and_floor():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rng = random.Random(42)
    state = rng.getstate()
    assert next_slot(None, now, 30, rng) == now
    assert rng.getstate() == state
    expected = now + timedelta(minutes=random.Random(42).uniform(30, 90))
    assert next_slot(now, now, 30, rng) == expected
    for seed in range(20):
        assert now + timedelta(minutes=30) <= next_slot(now, now, 30, random.Random(seed)) <= now + timedelta(minutes=90)
    assert next_slot(now - timedelta(days=1), now, 30, random.Random(1)) == now
    assert next_slot(now, now, 0, random.Random(1)) == now


@pytest.mark.parametrize("cap,sent,expected", [(5, 2, 3), (5, 5, 0), (5, 8, 0), (0, 0, 0)])
def test_remaining(cap, sent, expected):
    assert remaining_today(cap, sent) == expected
