"""Unit tests for pure audience publishing schedule helper."""

from datetime import datetime, timedelta, timezone
import zoneinfo
import pytest

from app.services.publishing_schedule import (
    compute_audience_slot,
    get_hour_weight,
    DEFAULT_ACTIVE_START_HOUR,
    DEFAULT_ACTIVE_END_HOUR,
)
from app.services.social_delivery_policy import compute_next_slot


def test_regression_old_policy_exact_5min_burst_demonstration():
    """Demonstrate existing compute_next_slot schedules 6 consecutive posts at exact 5-minute intervals."""
    # Production Formula Sydney parameters
    tz_name = "Australia/Sydney"
    now_utc = datetime(2026, 10, 20, 0, 48, 35, tzinfo=timezone.utc)
    daily_target = 31
    min_gap = 5

    slots = []
    current_slots = []
    for _ in range(6):
        s = compute_next_slot(
            now_utc=now_utc,
            timezone_name=tz_name,
            daily_target=daily_target,
            min_gap_minutes=min_gap,
            existing_slots_utc=current_slots,
            sleep_start_hour=22,
            sleep_end_hour=8,
        )
        slots.append(s)
        current_slots.append(s)

    # In old compute_next_slot, all 5 consecutive gaps are EXACTLY 5 minutes (300 seconds)
    gaps = [(slots[i + 1] - slots[i]).total_seconds() for i in range(5)]
    assert gaps == [300.0, 300.0, 300.0, 300.0, 300.0], "Old policy produced exact 5-min burst"


def test_audience_slot_formula_sydney_no_5min_burst():
    """Formula Sydney 6 consecutive posts queued at once must NOT all be 5 min apart."""
    tz_name = "Australia/Sydney"
    sydney_tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = datetime(2026, 10, 20, 0, 48, 35, tzinfo=timezone.utc)  # 11:48:35 AEDT
    daily_target = 31
    min_gap = 5

    slots = []
    current_slots = []
    for i in range(6):
        s = compute_audience_slot(
            now_utc=now_utc,
            timezone_name=tz_name,
            daily_target=daily_target,
            min_gap_minutes=min_gap,
            existing_slots_utc=current_slots,
            sleep_start_hour=22,
            sleep_end_hour=8,
            namespace="formula-dest-4",
        )
        slots.append(s)
        current_slots.append(s)

    for s in slots:
        assert s >= now_utc + timedelta(seconds=60)
        s_local = s.astimezone(sydney_tz)
        assert 8 <= s_local.hour < 22

    gaps = [(slots[i + 1] - slots[i]).total_seconds() for i in range(5)]
    assert any(g != 300.0 for g in gaps), "Slots must not all be 5 min apart"
    for g in gaps:
        assert g >= 300.0
    avg_gap_minutes = (sum(gaps) / len(gaps)) / 60.0
    assert avg_gap_minutes > 15.0, f"Expected varied spacing across active hours, got avg {avg_gap_minutes}m"


def test_audience_slot_jobs_arriving_5min_apart_no_5min_chain():
    """Verify that 6 jobs arriving exactly 5 minutes apart do NOT produce a 5-minute schedule chain."""
    tz_name = "Australia/Sydney"
    sydney_tz = zoneinfo.ZoneInfo(tz_name)
    base_now_utc = datetime(2026, 10, 20, 0, 48, 35, tzinfo=timezone.utc)
    daily_target = 31
    min_gap = 5

    slots = []
    current_slots = []
    # 6 jobs arrive at T, T+5m, T+10m, T+15m, T+20m, T+25m
    for i in range(6):
        arrival_now_utc = base_now_utc + timedelta(minutes=5 * i)
        s = compute_audience_slot(
            now_utc=arrival_now_utc,
            timezone_name=tz_name,
            daily_target=daily_target,
            min_gap_minutes=min_gap,
            existing_slots_utc=current_slots,
            sleep_start_hour=22,
            sleep_end_hour=8,
            namespace="formula-dest-4",
        )
        slots.append(s)
        current_slots.append(s)

    # Gaps between scheduled slots must NOT all be 5 minutes (300 seconds)
    gaps = [(slots[j + 1] - slots[j]).total_seconds() for j in range(5)]
    assert any(g != 300.0 for g in gaps), f"Jobs arriving 5m apart formed 5m chain: {gaps}"
    # Every gap must respect the 5 min min_gap floor
    for g in gaps:
        assert g >= 300.0
    # Average gap should reflect quantile day pacing
    avg_gap_minutes = (sum(gaps) / len(gaps)) / 60.0
    assert avg_gap_minutes > 15.0, f"Expected distributed pacing, got avg {avg_gap_minutes}m"


def test_audience_slot_default_active_window_when_no_sleep():
    """When sleep hours are None, default active window 07:00-22:00 is enforced."""
    tz_name = "UTC"
    now_utc = datetime(2026, 10, 20, 23, 30, tzinfo=timezone.utc)  # Night time
    daily_target = 10
    min_gap = 15

    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[],
        sleep_start_hour=None,
        sleep_end_hour=None,
    )
    # Must be rolled to tomorrow morning >= 07:00
    assert slot >= now_utc + timedelta(seconds=60)
    assert 7 <= slot.hour < 22


def test_audience_slot_equal_sleep_start_end_disables_sleep():
    """Equal sleep start and end hours means no sleep restriction (24h active)."""
    tz_name = "UTC"
    now_utc = datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc)
    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=24,
        min_gap_minutes=5,
        existing_slots_utc=[],
        sleep_start_hour=0,
        sleep_end_hour=0,
    )
    assert slot >= now_utc + timedelta(seconds=60)


def test_audience_slot_weighted_heuristic_midday_and_evening():
    """Heuristic gives midday (12-14) and evening (18-21) higher weight than early morning."""
    assert get_hour_weight(12) > get_hour_weight(9)
    assert get_hour_weight(19) > get_hour_weight(9)
    assert get_hour_weight(13) == get_hour_weight(12)
    assert get_hour_weight(20) == get_hour_weight(18)


def test_audience_slot_future_backlog_does_not_block_earlier_day():
    """A slot booked 3 days ahead must not block scheduling into today or tomorrow."""
    tz_name = "Asia/Jakarta"
    jakarta_tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)  # 08:00 Jakarta

    # Far future slot on 2026-10-23
    future_slot = datetime(2026, 10, 23, 10, 0, tzinfo=timezone.utc)

    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=5,
        min_gap_minutes=30,
        existing_slots_utc=[future_slot],
    )
    # Slot must land on today (2026-10-20), unblocked by the future booking
    assert slot.astimezone(jakarta_tz).date() == datetime(2026, 10, 20).date()


def test_audience_slot_dst_transition_london_autumn():
    """Handles Europe/London autumn DST transition (clock fallback)."""
    tz_name = "Europe/London"
    london_tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = datetime(2026, 10, 24, 23, 0, tzinfo=timezone.utc)

    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=4,
        min_gap_minutes=60,
        existing_slots_utc=[],
        sleep_start_hour=0,
        sleep_end_hour=6,
    )
    assert slot.tzinfo == timezone.utc
    slot_london = slot.astimezone(london_tz)
    assert slot_london.hour >= 6


def test_audience_slot_daily_cap_rollover():
    """When daily target is reached for local calendar day, rolls over to next local day."""
    tz_name = "Asia/Jakarta"
    jakarta_tz = zoneinfo.ZoneInfo(tz_name)
    now_utc = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)  # 08:00 Jakarta
    daily_target = 2
    min_gap = 60

    slot1 = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[],
    )
    slot2 = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[slot1],
    )
    slot3 = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=tz_name,
        daily_target=daily_target,
        min_gap_minutes=min_gap,
        existing_slots_utc=[slot1, slot2],
    )

    assert slot1.astimezone(jakarta_tz).date() == datetime(2026, 10, 20).date()
    assert slot2.astimezone(jakarta_tz).date() == datetime(2026, 10, 20).date()
    assert slot3.astimezone(jakarta_tz).date() > datetime(2026, 10, 20).date()


def test_audience_slot_deterministic_jitter():
    """Identical input yields identical slot; different namespace yields different minute."""
    now_utc = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)
    s1 = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=15,
        existing_slots_utc=[],
        namespace="dest-A",
    )
    s2 = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=15,
        existing_slots_utc=[],
        namespace="dest-A",
    )
    s3 = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=15,
        existing_slots_utc=[],
        namespace="dest-B",
    )
    assert s1 == s2, "Deterministic schedule must return exact same slot"
    # Different namespace gets different jitter
    assert s1.minute != s3.minute or s1.second != s3.second


def test_audience_slot_input_validation():
    """Rejects invalid inputs with clear ValueError."""
    valid_now = datetime(2026, 10, 20, 1, 0, tzinfo=timezone.utc)

    # Naive now_utc
    with pytest.raises(ValueError, match="aware"):
        compute_audience_slot(
            now_utc=datetime(2026, 10, 20, 1, 0),
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=10,
            existing_slots_utc=[],
        )

    # Invalid timezone
    with pytest.raises(ValueError, match="timezone"):
        compute_audience_slot(
            now_utc=valid_now,
            timezone_name="Invalid/Timezone",
            daily_target=5,
            min_gap_minutes=10,
            existing_slots_utc=[],
        )

    # Invalid daily target
    with pytest.raises(ValueError, match="daily_target"):
        compute_audience_slot(
            now_utc=valid_now,
            timezone_name="UTC",
            daily_target=0,
            min_gap_minutes=10,
            existing_slots_utc=[],
        )
    with pytest.raises(ValueError, match="daily_target"):
        compute_audience_slot(
            now_utc=valid_now,
            timezone_name="UTC",
            daily_target=True,  # bool is subclass of int
            min_gap_minutes=10,
            existing_slots_utc=[],
        )

    # Negative min_gap
    with pytest.raises(ValueError, match="min_gap"):
        compute_audience_slot(
            now_utc=valid_now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=-1,
            existing_slots_utc=[],
        )

    # Asymmetric sleep hours
    with pytest.raises(ValueError, match="sleep"):
        compute_audience_slot(
            now_utc=valid_now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=10,
            existing_slots_utc=[],
            sleep_start_hour=22,
            sleep_end_hour=None,
        )
