"""Tests for publishing pacing: 15-minute gap floor, fitting_daily_target cap, and peak dominance."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import zoneinfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.mode_quota import pick_slot
from app.services.publishing_schedule import (
    _valid_day_candidates,
    compute_audience_slot,
    fitting_daily_target,
    get_hour_weight,
)

TZ_NAME = "Asia/Jakarta"
TARGET_TZ = zoneinfo.ZoneInfo(TZ_NAME)
SLEEP_START = 22
SLEEP_END = 7
# 2026-10-20 06:00 Jakarta is 2026-10-19 23:00 UTC
FIXED_NOW_UTC = datetime(2026, 10, 19, 23, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session():
    """In-memory SQLite session with social publishing tables."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


def test_get_hour_weight_peak_numbers():
    """13a: get_hour_weight returns 1.5 at 12, 2.0 at 19, 1.0 at 9."""
    assert get_hour_weight(12) == 1.5
    assert get_hour_weight(19) == 2.0
    assert get_hour_weight(9) == 1.0


def test_gap_floor_enforced_at_fifteen_minutes():
    """13b: min_gap_minutes=5, daily_target=20; all slots on first day >= 15 min apart."""
    slots: list[datetime] = []
    for _ in range(20):
        s = compute_audience_slot(
            now_utc=FIXED_NOW_UTC,
            timezone_name=TZ_NAME,
            daily_target=20,
            min_gap_minutes=5,
            existing_slots_utc=list(slots),
            sleep_start_hour=SLEEP_START,
            sleep_end_hour=SLEEP_END,
        )
        slots.append(s)

    first_day = FIXED_NOW_UTC.astimezone(TARGET_TZ).date()
    day_slots = sorted([s for s in slots if s.astimezone(TARGET_TZ).date() == first_day])
    assert len(day_slots) > 0
    for i in range(len(day_slots) - 1):
        diff_sec = (day_slots[i + 1] - day_slots[i]).total_seconds()
        assert diff_sec >= 15 * 60, f"Gap {diff_sec}s between slots is under 15 minutes"


def test_fitting_daily_target_cap_and_rollover():
    """13c: min_gap_minutes=60, daily_target=30; capped to fitting count on day 1, next on day 2."""
    fit = fitting_daily_target(30, 60, SLEEP_START, SLEEP_END)
    assert fit < 30

    slots: list[datetime] = []
    for _ in range(fit + 1):
        s = compute_audience_slot(
            now_utc=FIXED_NOW_UTC,
            timezone_name=TZ_NAME,
            daily_target=30,
            min_gap_minutes=60,
            existing_slots_utc=list(slots),
            sleep_start_hour=SLEEP_START,
            sleep_end_hour=SLEEP_END,
        )
        slots.append(s)

    first_day = FIXED_NOW_UTC.astimezone(TARGET_TZ).date()
    day1_slots = sorted([s for s in slots if s.astimezone(TARGET_TZ).date() == first_day])
    assert len(day1_slots) == fit
    next_day_slot = slots[fit]
    assert next_day_slot.astimezone(TARGET_TZ).date() == first_day + timedelta(days=1)

    for i in range(len(day1_slots) - 1):
        diff_sec = (day1_slots[i + 1] - day1_slots[i]).total_seconds()
        assert diff_sec >= 60 * 60, f"Gap {diff_sec}s on day 1 is under 60 minutes"


def test_prefer_peak_vs_earliest():
    """13d: prefer_peak=True returns local hour in 18..20; prefer_peak=False returns 7 or 8."""
    slot_peak = compute_audience_slot(
        now_utc=FIXED_NOW_UTC,
        timezone_name=TZ_NAME,
        daily_target=10,
        min_gap_minutes=30,
        existing_slots_utc=[],
        sleep_start_hour=SLEEP_START,
        sleep_end_hour=SLEEP_END,
        prefer_peak=True,
    )
    slot_earliest = compute_audience_slot(
        now_utc=FIXED_NOW_UTC,
        timezone_name=TZ_NAME,
        daily_target=10,
        min_gap_minutes=30,
        existing_slots_utc=[],
        sleep_start_hour=SLEEP_START,
        sleep_end_hour=SLEEP_END,
        prefer_peak=False,
    )

    peak_hour = slot_peak.astimezone(TARGET_TZ).hour
    early_hour = slot_earliest.astimezone(TARGET_TZ).hour

    assert peak_hour in (18, 19, 20), f"Expected peak hour in 18..20, got {peak_hour}"
    assert early_hour in (7, 8), f"Expected earliest hour in 7..8, got {early_hour}"


def test_peak_dominance_percentage():
    """13e: filling one day with prefer_peak=True has >= 40% slots in 12-13 or 18-20."""
    slots: list[datetime] = []
    for _ in range(10):
        s = compute_audience_slot(
            now_utc=FIXED_NOW_UTC,
            timezone_name=TZ_NAME,
            daily_target=10,
            min_gap_minutes=30,
            existing_slots_utc=list(slots),
            sleep_start_hour=SLEEP_START,
            sleep_end_hour=SLEEP_END,
            prefer_peak=True,
        )
        slots.append(s)

    first_day = FIXED_NOW_UTC.astimezone(TARGET_TZ).date()
    day_slots = [s for s in slots if s.astimezone(TARGET_TZ).date() == first_day]
    peak_hours = {12, 13, 18, 19, 20}
    peak_count = sum(1 for s in day_slots if s.astimezone(TARGET_TZ).hour in peak_hours)

    pct = peak_count / len(day_slots)
    assert pct >= 0.40, f"Peak fraction {pct:.1%} is below 40%"


def test_hash_stability_wide_cells():
    """13f: wide cell quantile gives identical candidate minutes as 0.15 + 0.70 * rnd."""
    first_day = FIXED_NOW_UTC.astimezone(TARGET_TZ).date()
    cands, minute_weights = _valid_day_candidates(
        current_date=first_day,
        target_tz=TARGET_TZ,
        daily_target=4,
        min_gap_minutes=15,
        clean_existing=[],
        min_lead_utc=FIXED_NOW_UTC,
        sleep_start_hour=SLEEP_START,
        sleep_end_hour=SLEEP_END,
        namespace="stability",
    )

    active_minutes = [m for m in range(1440) if minute_weights[m] > 0]
    cum_weights: list[float] = []
    acc = 0.0
    for m in active_minutes:
        acc += minute_weights[m]
        cum_weights.append(acc)

    tot = cum_weights[-1]
    t_start = 0.0
    t_end = tot / 4.0
    k_start = 0
    while k_start < len(active_minutes) and cum_weights[k_start] < t_start:
        k_start += 1
    k_end = k_start
    while k_end < len(active_minutes) and cum_weights[k_end] < t_end:
        k_end += 1

    h = hashlib.sha256(f"{first_day.isoformat()}:stability:0".encode("utf-8")).digest()
    rnd = int.from_bytes(h[:8], "big") / 0xFFFFFFFFFFFFFFFF
    expected_jitter = 0.15 + 0.70 * rnd
    expected_k = int(round(k_start + expected_jitter * (k_end - k_start)))
    expected_minute = active_minutes[expected_k]

    actual_loc = cands[0].astimezone(TARGET_TZ)
    actual_minute = actual_loc.hour * 60 + actual_loc.minute

    assert expected_minute == actual_minute


def test_mode_quota_news_earliest_vs_ig_peak(db_session: Session):
    """13g: news job gets earliest slot and ig job on same empty destination gets evening slot."""
    project = TargetFanpage(
        name="Pacing Fanpage",
        timezone=TZ_NAME,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Pacing Dest",
        mode="repliz",
        timezone=TZ_NAME,
        daily_target=10,
        min_gap_minutes=30,
        is_active=True,
        mode_shares={"news": 50, "ig": 50},
    )
    db_session.add(dest)
    db_session.flush()

    news_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        created_at=FIXED_NOW_UTC.replace(tzinfo=None),
    )
    db_session.add(news_job)
    db_session.flush()

    ig_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        created_at=FIXED_NOW_UTC.replace(tzinfo=None),
    )
    db_session.add(ig_job)
    db_session.flush()

    slot_news = pick_slot(
        db=db_session,
        destination=dest,
        job=news_job,
        fanpage=project,
        now_utc=FIXED_NOW_UTC,
        all_reservations=[],
        sleep_start=SLEEP_START,
        sleep_end=SLEEP_END,
    )

    slot_ig = pick_slot(
        db=db_session,
        destination=dest,
        job=ig_job,
        fanpage=project,
        now_utc=FIXED_NOW_UTC,
        all_reservations=[],
        sleep_start=SLEEP_START,
        sleep_end=SLEEP_END,
    )

    news_hr = slot_news.astimezone(TARGET_TZ).hour
    ig_hr = slot_ig.astimezone(TARGET_TZ).hour

    assert news_hr in (7, 8), f"Expected news slot in 7..8, got {news_hr}"
    assert ig_hr in (18, 19, 20), f"Expected ig slot in 18..20, got {ig_hr}"
