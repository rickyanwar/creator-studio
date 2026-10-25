from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3
from unittest.mock import MagicMock

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
from app.services.day_plan import destination_day_plan
from app.services.event_days import (
    effective_daily_target,
    event_multiplier,
    news_fresh_hours,
)
from app.services.mode_quota import pick_slot


@pytest.fixture
def db_session():
    """Create in-memory SQLite database for testing."""
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


def test_event_multiplier_logic(monkeypatch):
    """1. event_multiplier returns 2 when _event_boosted_daily_limit doubles,

    1 otherwise, 1 for fanpage None, 1 when it raises.
    """
    db = MagicMock()
    fanpage = MagicMock()
    day = date(2026, 10, 26)

    # 1. doubles -> returns 2
    monkeypatch.setattr(
        "app.tasks.publisher._event_boosted_daily_limit",
        lambda db, fp, base, d: 2 * base,
    )
    assert event_multiplier(db, fanpage, day) == 2

    # 2. otherwise -> returns 1
    monkeypatch.setattr(
        "app.tasks.publisher._event_boosted_daily_limit",
        lambda db, fp, base, d: base,
    )
    assert event_multiplier(db, fanpage, day) == 1

    # 3. fanpage is None -> returns 1
    assert event_multiplier(db, None, day) == 1

    # 4. when it raises -> returns 1
    def _raise(*args, **kwargs):
        raise RuntimeError("database unreachable")

    monkeypatch.setattr("app.tasks.publisher._event_boosted_daily_limit", _raise)
    assert event_multiplier(db, fanpage, day) == 1


def test_news_fresh_hours_logic(monkeypatch):
    """2. news_fresh_hours: base 24 -> 6 on event day, 12 otherwise;

    base 4 -> 4 both; base None -> 6/12.
    """
    db = MagicMock()
    fanpage = MagicMock()
    day = date(2026, 10, 26)

    # Event day (multiplier > 1)
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args: 2)
    assert news_fresh_hours(db, fanpage, 24, day) == 6
    assert news_fresh_hours(db, fanpage, 4, day) == 4
    assert news_fresh_hours(db, fanpage, None, day) == 6

    # Normal day (multiplier == 1)
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args: 1)
    assert news_fresh_hours(db, fanpage, 24, day) == 12
    assert news_fresh_hours(db, fanpage, 4, day) == 4
    assert news_fresh_hours(db, fanpage, None, day) == 12


def test_pick_slot_race_day_boost(db_session: Session, monkeypatch):
    """3. pick_slot on an event day with daily_target=4, mode_shares={'news': 100}

    and 4 news reservations already today returns a slot TODAY (boost gives 8);
    the same setup on a normal day returns a slot on a later day.
    """
    tz = "UTC"
    project = TargetFanpage(
        name="Boost Test Project",
        timezone=tz,
        is_active=True,
        mode2_news_content_enabled=True,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Boost Test Dest",
        mode="repliz",
        timezone=tz,
        daily_target=4,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"news": 100},
        borrow_hours_before_sleep=0,
    )
    db_session.add(dest)
    db_session.flush()

    today_date = date(2026, 10, 26)
    t0 = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    jobs = [
        PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.news_content,
            created_at=t0.replace(tzinfo=None),
            is_deleted=False,
        )
        for _ in range(4)
    ]
    db_session.add_all(jobs)
    db_session.flush()

    slots_today = [
        datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc),
    ]
    deliveries = [
        PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="scheduled",
            scheduled_for=s.replace(tzinfo=None),
        )
        for job, s in zip(jobs, slots_today)
    ]
    db_session.add_all(deliveries)
    db_session.flush()

    new_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        created_at=t0.replace(tzinfo=None),
    )
    db_session.add(new_job)
    db_session.flush()

    # Normal day: event_multiplier returns 1 -> daily_target remains 4.
    # All 4 slots are used today, so pick_slot must return a slot on a later day.
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args: 1)
    slot_normal = pick_slot(
        db=db_session,
        destination=dest,
        job=new_job,
        fanpage=project,
        now_utc=t0,
        all_reservations=slots_today,
    )
    assert slot_normal.date() > today_date

    # Event day: event_multiplier returns 2 -> daily_target doubled to 8.
    # 4 slots used < 8 target, so pick_slot returns a slot TODAY.
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args: 2)
    slot_event = pick_slot(
        db=db_session,
        destination=dest,
        job=new_job,
        fanpage=project,
        now_utc=t0,
        all_reservations=slots_today,
    )
    assert slot_event.date() == today_date


def test_destination_day_plan_event_day(db_session: Session, monkeypatch):
    """4. destination_day_plan on an event day returns daily_target == 2 * base

    and base_daily_target == base.
    """
    tz = "UTC"
    project = TargetFanpage(
        name="Day Plan Boost Fanpage",
        timezone=tz,
        is_active=True,
        mode2_news_content_enabled=True,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Day Plan Boost Dest",
        mode="repliz",
        timezone=tz,
        daily_target=5,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"news": 100},
    )
    db_session.add(dest)
    db_session.flush()

    t0 = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    # Event day: multiplier 2
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args: 2)
    plan_event = destination_day_plan(db_session, dest, now_utc=t0)
    assert plan_event["daily_target"] == 10
    assert plan_event["base_daily_target"] == 5

    # Normal day: multiplier 1
    monkeypatch.setattr("app.services.event_days.event_multiplier", lambda *args: 1)
    plan_normal = destination_day_plan(db_session, dest, now_utc=t0)
    assert plan_normal["daily_target"] == 5
    assert plan_normal["base_daily_target"] == 5
