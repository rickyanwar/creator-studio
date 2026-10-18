"""Unit tests for Strategy best_hours proposals and weighted slot planning."""

from datetime import datetime, timedelta, timezone
import zoneinfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "JSON"

from app.database import Base
from app.models.strategy import StrategyRecommendation
from app.models.target_fanpages import TargetFanpage
from app.services.publishing_schedule import compute_audience_slot
from app.services.social_delivery_policy import compute_next_slot
from app.services.strategy import approve, create_recommendation, validate_proposal


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    StrategyRecommendation.__table__.c.created_at.server_default = None
    TargetFanpage.__table__.create(bind=engine)
    StrategyRecommendation.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def fanpage(db_session):
    fp = TargetFanpage(
        repliz_account_id="fp_hour_weights_123",
        name="Test Brand Weights",
        publish_daily_limit=10,
        publish_sleep_start_hour=1,
        publish_sleep_end_hour=6,
        timezone="Europe/London",
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    return fp


# ==============================================================================
# 1. Strategy best_hours proposal and approve
# ==============================================================================


def test_validate_proposal_best_hours():
    """best_hours proposal accepts best_hours list, weights dict, or general dict."""
    validate_proposal("best_hours", {"best_hours": [18, 19, 20]})
    validate_proposal("best_hours", {"best_hours": [19], "weights": {"19": 2.5}})
    validate_proposal("best_hours", {"weights": {"19": 2.0}})
    validate_proposal("best_hours", {"any": "thing"})


def test_approve_best_hours_list_only(db_session, fanpage):
    """When proposal has only best_hours list, approve writes 2.0 for them and 1.0 for others."""
    rec = create_recommendation(
        db_session,
        fanpage_id=fanpage.id,
        kind="best_hours",
        title="Best hours recommendation",
        proposal={"best_hours": [19, 20]},
        rationale="High engagement in evening",
        evidence={"sample_posts": 15},
        token_id=None,
    )
    assert rec.status == "proposed"

    approve(db_session, rec, "admin_user")

    db_session.refresh(rec)
    db_session.refresh(fanpage)

    assert rec.status == "applied"
    assert rec.decided_by == "admin_user"
    assert rec.previous_values == {"publish_hour_weights": None}

    weights = fanpage.publish_hour_weights
    assert isinstance(weights, dict)
    assert len(weights) == 24
    assert weights["19"] == 2.0
    assert weights["20"] == 2.0
    assert weights["0"] == 1.0
    assert weights["12"] == 1.0


def test_approve_best_hours_with_custom_weights(db_session, fanpage):
    """When proposal includes weights, approve writes those weights."""
    fanpage.publish_hour_weights = {"12": 1.5}
    db_session.commit()

    rec = create_recommendation(
        db_session,
        fanpage_id=fanpage.id,
        kind="best_hours",
        title="Custom weights recommendation",
        proposal={
            "best_hours": [18, 19],
            "weights": {"18": 2.5, "19": 3.0, "21": 1.8},
        },
        rationale="Peak audience times",
        evidence={},
        token_id=None,
    )

    approve(db_session, rec, "admin_user")

    db_session.refresh(rec)
    db_session.refresh(fanpage)

    assert rec.status == "applied"
    assert rec.previous_values == {"publish_hour_weights": {"12": 1.5}}

    weights = fanpage.publish_hour_weights
    assert weights["18"] == 2.5
    assert weights["19"] == 3.0
    assert weights["21"] == 1.8
    assert weights["10"] == 1.0


# ==============================================================================
# 2. Slot planner: preferred heavier hours
# ==============================================================================


def test_planner_with_weights_prefers_heavier_hours():
    """When hour 19 has weight 2.0 and earlier hours have 1.0, single slot prefers hour 19."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    weights = {"19": 2.0}

    slot = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=1,
        min_gap_minutes=60,
        existing_slots_utc=[],
        publish_hour_weights=weights,
    )

    assert slot == datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)


def test_planner_with_weights_spreads_across_heavier_hours():
    """Daily target 2 picks the top two weighted hours."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    weights = {"18": 2.5, "20": 2.5}

    slot1 = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=2,
        min_gap_minutes=60,
        existing_slots_utc=[],
        publish_hour_weights=weights,
    )
    assert slot1 == datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)

    slot2 = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=2,
        min_gap_minutes=60,
        existing_slots_utc=[slot1],
        publish_hour_weights=weights,
    )
    assert slot2 == datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)


# ==============================================================================
# 3. Slot planner: hard rules stay intact
# ==============================================================================


def test_planner_with_weights_respects_quiet_hours():
    """Heavy hour inside sleep window is skipped; next best allowed hour is chosen."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    # Hour 23 has weight 3.0, but sleep window is 22:00 to 08:00
    weights = {"23": 3.0, "19": 2.0}

    slot = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=1,
        min_gap_minutes=60,
        existing_slots_utc=[],
        sleep_start_hour=22,
        sleep_end_hour=8,
        publish_hour_weights=weights,
    )

    # 23:00 is sleeping, so 19:00 must be chosen
    assert slot == datetime(2026, 10, 20, 19, 0, tzinfo=timezone.utc)
    assert slot.hour not in [22, 23, 0, 1, 2, 3, 4, 5, 6, 7]


def test_planner_with_weights_respects_posts_per_day():
    """When daily target is reached on local day, planner rolls over to next local day."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    weights = {"18": 2.0, "20": 2.0}

    slot1 = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)
    slot2 = datetime(2026, 10, 20, 20, 0, tzinfo=timezone.utc)

    # Day 2026-10-20 has already reached daily_target = 2
    slot3 = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=2,
        min_gap_minutes=60,
        existing_slots_utc=[slot1, slot2],
        publish_hour_weights=weights,
    )

    assert slot3.date() == datetime(2026, 10, 21).date()
    assert slot3 == datetime(2026, 10, 21, 18, 0, tzinfo=timezone.utc)


def test_planner_with_weights_respects_minimum_gap():
    """Slot must respect min_gap_minutes from existing slots even if within heavy hour."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    weights = {"19": 3.0}
    # Existing slot at 18:45
    existing_slot = datetime(2026, 10, 20, 18, 45, tzinfo=timezone.utc)
    min_gap = 60

    slot = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=2,
        min_gap_minutes=min_gap,
        existing_slots_utc=[existing_slot],
        publish_hour_weights=weights,
    )

    # 18:45 + 60 min = 19:45
    assert (slot - existing_slot).total_seconds() >= min_gap * 60
    assert slot == datetime(2026, 10, 20, 19, 45, tzinfo=timezone.utc)


def test_planner_respects_destination_timezone():
    """Hour weights apply in the brand destination's local timezone."""
    # Asia/Tokyo is UTC+9. Hour 19 local = 10:00 UTC
    tokyo_tz = zoneinfo.ZoneInfo("Asia/Tokyo")
    now_utc = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)  # 09:00 Tokyo
    weights = {"19": 2.5}

    slot_utc = compute_next_slot(
        now_utc=now_utc,
        timezone_name="Asia/Tokyo",
        daily_target=1,
        min_gap_minutes=60,
        existing_slots_utc=[],
        publish_hour_weights=weights,
    )

    slot_local = slot_utc.astimezone(tokyo_tz)
    assert slot_local.hour == 19
    assert slot_utc == datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)


# ==============================================================================
# 4. Without weights: identical to previous behaviour
# ==============================================================================


def test_planner_without_weights_identical_to_before():
    """With no weights passed, compute_next_slot behaves exactly as before."""
    now_utc = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

    # 1. Basic future slot
    res_none = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=60,
        existing_slots_utc=[],
        publish_hour_weights=None,
    )
    assert res_none == now_utc

    # 2. Minimum gap spacing
    existing = datetime(2026, 10, 20, 11, 30, tzinfo=timezone.utc)
    res_gap = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=45,
        existing_slots_utc=[existing],
        publish_hour_weights=None,
    )
    assert res_gap == existing + timedelta(minutes=45)

    # 3. Sleep window advance
    res_sleep = compute_next_slot(
        now_utc=datetime(2026, 10, 20, 2, 0, tzinfo=timezone.utc),
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=30,
        existing_slots_utc=[],
        sleep_start_hour=0,
        sleep_end_hour=6,
        publish_hour_weights=None,
    )
    assert res_sleep == datetime(2026, 10, 20, 6, 0, tzinfo=timezone.utc)

    # 4. Daily cap rollover
    res_cap = compute_next_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=1,
        min_gap_minutes=30,
        existing_slots_utc=[datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)],
        publish_hour_weights=None,
    )
    assert res_cap == datetime(2026, 10, 21, 0, 0, tzinfo=timezone.utc)


# ==============================================================================
# 5. Live channel publishing schedule (compute_audience_slot) with hour_weights
# ==============================================================================


def test_compute_audience_slot_with_weights_picks_heavier_hour():
    """compute_audience_slot with hour_weights prefers candidates in heavier local hours."""
    now_utc = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    # Baseline without weights: earliest valid candidate is at 12:07 UTC
    slot_unweighted = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=4,
        min_gap_minutes=60,
        existing_slots_utc=[],
    )
    assert slot_unweighted == datetime(2026, 10, 20, 12, 7, tzinfo=timezone.utc)

    # With hour 19 heavily weighted (and others low/zero), candidate at 19:35 is chosen
    slot_weighted = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=4,
        min_gap_minutes=60,
        existing_slots_utc=[],
        hour_weights={"19": 100.0, "12": 0.0, "17": 0.0},
    )
    assert slot_weighted == datetime(2026, 10, 20, 19, 35, tzinfo=timezone.utc)
    assert slot_weighted.hour == 19


def test_compute_audience_slot_never_inside_quiet_hours():
    """compute_audience_slot with heavy weights inside sleep window never picks a slot in sleep hours."""
    now_utc = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    # Heavy weight given to hour 23 and hour 1, but sleep window is 22:00 to 07:00
    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=4,
        min_gap_minutes=60,
        existing_slots_utc=[],
        sleep_start_hour=22,
        sleep_end_hour=7,
        hour_weights={"23": 1000.0, "1": 1000.0, "17": 5.0},
    )
    # The slot must not be in quiet hours [22, 7)
    assert not (slot.hour >= 22 or slot.hour < 7)
    assert slot.hour in [12, 17, 19]


def test_compute_audience_slot_never_closer_than_min_gap():
    """compute_audience_slot respects min_gap_minutes separation from all existing slots."""
    now_utc = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    min_gap = 90
    # Existing slot at 19:30
    existing_slot = datetime(2026, 10, 20, 19, 30, tzinfo=timezone.utc)
    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=4,
        min_gap_minutes=min_gap,
        existing_slots_utc=[existing_slot],
        hour_weights={"19": 1000.0},
    )
    # Candidate near 19:35 is rejected because abs diff (5 min) < 90 min.
    # Next valid candidate (12:07 or 17:49) is selected instead.
    diff_minutes = abs((slot - existing_slot).total_seconds()) / 60.0
    assert diff_minutes >= min_gap


def test_compute_audience_slot_respects_daily_target():
    """When daily target is reached on local day, planner rolls over to next local day."""
    now_utc = datetime(2026, 10, 20, 8, 0, tzinfo=timezone.utc)
    # daily_target = 2. Existing slots fill day 2026-10-20.
    s1 = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    s2 = datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)

    slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name="UTC",
        daily_target=2,
        min_gap_minutes=60,
        existing_slots_utc=[s1, s2],
        hour_weights={"19": 10.0},
    )
    # Rollover to 2026-10-21
    assert slot.date() == datetime(2026, 10, 21, tzinfo=timezone.utc).date()


def test_compute_audience_slot_weights_none_identical_grid():
    """Grid of 50 random inputs produces 100% identical results with hour_weights=None vs without."""
    import random
    rng = random.Random(42)

    timezones = ["UTC", "Europe/London", "America/New_York", "Asia/Tokyo", "Australia/Sydney"]

    for i in range(50):
        tz_name = rng.choice(timezones)
        base_year = 2026
        base_month = rng.randint(1, 12)
        base_day = rng.randint(1, 28)
        base_hour = rng.randint(0, 23)
        base_minute = rng.randint(0, 59)
        now_utc = datetime(base_year, base_month, base_day, base_hour, base_minute, tzinfo=timezone.utc)

        daily_target = rng.randint(1, 8)
        min_gap_minutes = rng.choice([15, 30, 45, 60, 90, 120])
        namespace = f"grid-seed-{i}"

        has_sleep = rng.choice([True, False])
        sleep_start = rng.randint(20, 23) if has_sleep else None
        sleep_end = rng.randint(5, 8) if has_sleep else None

        num_existing = rng.randint(0, 3)
        existing_slots = []
        for _ in range(num_existing):
            ex_dt = now_utc + timedelta(hours=rng.randint(2, 24))
            existing_slots.append(ex_dt)

        slot_baseline = compute_audience_slot(
            now_utc=now_utc,
            timezone_name=tz_name,
            daily_target=daily_target,
            min_gap_minutes=min_gap_minutes,
            existing_slots_utc=existing_slots,
            sleep_start_hour=sleep_start,
            sleep_end_hour=sleep_end,
            namespace=namespace,
        )

        slot_with_none = compute_audience_slot(
            now_utc=now_utc,
            timezone_name=tz_name,
            daily_target=daily_target,
            min_gap_minutes=min_gap_minutes,
            existing_slots_utc=existing_slots,
            sleep_start_hour=sleep_start,
            sleep_end_hour=sleep_end,
            namespace=namespace,
            hour_weights=None,
        )

        slot_with_empty = compute_audience_slot(
            now_utc=now_utc,
            timezone_name=tz_name,
            daily_target=daily_target,
            min_gap_minutes=min_gap_minutes,
            existing_slots_utc=existing_slots,
            sleep_start_hour=sleep_start,
            sleep_end_hour=sleep_end,
            namespace=namespace,
            hour_weights={},
        )

        assert slot_baseline == slot_with_none
        assert slot_baseline == slot_with_empty
