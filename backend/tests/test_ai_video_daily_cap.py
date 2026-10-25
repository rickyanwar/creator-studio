from __future__ import annotations

from datetime import date, datetime, timezone
import json
import sqlite3
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
from app.services.mode_quota import ai_video_daily_cap, pick_slot


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


def test_ai_video_daily_cap_helper():
    """Unit test for ai_video_daily_cap helper."""
    assert ai_video_daily_cap(None) is None

    class MockFanpage:
        def __init__(self, count):
            self.ai_video_daily_count = count

    assert ai_video_daily_cap(MockFanpage(3)) == 3
    assert ai_video_daily_cap(MockFanpage(1)) == 1
    assert ai_video_daily_cap(MockFanpage(0)) is None
    assert ai_video_daily_cap(MockFanpage(-2)) is None
    assert ai_video_daily_cap(MockFanpage(None)) is None
    assert ai_video_daily_cap(object()) is None


def test_no_mode_shares_cap_exceeded_next_day_news_today(db_session: Session):
    """1. No mode_shares, daily_target=10, fan page ai_video_daily_count=3,

    3 ai_video reservations today -> next ai_video slot is on a later local day;
    a news job still gets a slot today.
    """
    tz = "UTC"
    today_date = date(2026, 10, 26)
    now_utc = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    project = TargetFanpage(
        name="Plain Cap Fanpage",
        timezone=tz,
        is_active=True,
        ai_video_enabled=True,
        mode2_news_content_enabled=True,
        ai_video_daily_count=3,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Plain Dest",
        mode="repliz",
        timezone=tz,
        daily_target=10,
        min_gap_minutes=5,
        is_active=True,
        mode_shares=None,
    )
    db_session.add(dest)
    db_session.flush()

    slots_today = [
        datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc),
    ]
    for s in slots_today:
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            created_at=now_utc.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="scheduled",
            scheduled_for=s.replace(tzinfo=None),
        )
        db_session.add(deliv)
    db_session.flush()

    # Next ai_video job must land on a later day because today already has 3
    ai_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ai_video,
        created_at=now_utc.replace(tzinfo=None),
    )
    db_session.add(ai_job)
    db_session.flush()

    slot_ai = pick_slot(
        db=db_session,
        destination=dest,
        job=ai_job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=slots_today,
    )
    assert slot_ai.date() > today_date

    # News job still gets a slot today (3 < daily_target of 10)
    news_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        created_at=now_utc.replace(tzinfo=None),
    )
    db_session.add(news_job)
    db_session.flush()

    slot_news = pick_slot(
        db=db_session,
        destination=dest,
        job=news_job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=slots_today,
    )
    assert slot_news.date() == today_date


def test_no_mode_shares_under_cap_slot_today(db_session: Session):
    """2. Same with 2 ai_video reservations today -> slot today."""
    tz = "UTC"
    today_date = date(2026, 10, 26)
    now_utc = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    project = TargetFanpage(
        name="Under Cap Fanpage",
        timezone=tz,
        is_active=True,
        ai_video_enabled=True,
        ai_video_daily_count=3,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Plain Dest 2",
        mode="repliz",
        timezone=tz,
        daily_target=10,
        min_gap_minutes=5,
        is_active=True,
        mode_shares=None,
    )
    db_session.add(dest)
    db_session.flush()

    slots_today = [
        datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc),
    ]
    for s in slots_today:
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            created_at=now_utc.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="scheduled",
            scheduled_for=s.replace(tzinfo=None),
        )
        db_session.add(deliv)
    db_session.flush()

    ai_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ai_video,
        created_at=now_utc.replace(tzinfo=None),
    )
    db_session.add(ai_job)
    db_session.flush()

    slot_ai = pick_slot(
        db=db_session,
        destination=dest,
        job=ai_job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=slots_today,
    )
    assert slot_ai.date() == today_date


def test_mode_shares_cap_cannot_borrow(db_session: Session):
    """3. With mode_shares {'news': 60, 'ai_video': 40}, daily_target=20 (ai_video quota 8)

    and cap 3 -> after 3 ai_video reservations today the next one goes to another day,
    even with free news quota to borrow.
    """
    tz = "UTC"
    today_date = date(2026, 10, 26)
    now_utc = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    project = TargetFanpage(
        name="Mode Shares Cap Fanpage",
        timezone=tz,
        is_active=True,
        ai_video_enabled=True,
        mode2_news_content_enabled=True,
        ai_video_daily_count=3,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Shares Dest",
        mode="repliz",
        timezone=tz,
        daily_target=20,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"news": 60, "ai_video": 40},
        borrow_hours_before_sleep=3,
    )
    db_session.add(dest)
    db_session.flush()

    slots_today = [
        datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc),
    ]
    for s in slots_today:
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            created_at=now_utc.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="scheduled",
            scheduled_for=s.replace(tzinfo=None),
        )
        db_session.add(deliv)
    db_session.flush()

    ai_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ai_video,
        created_at=now_utc.replace(tzinfo=None),
    )
    db_session.add(ai_job)
    db_session.flush()

    # Next ai_video must NOT borrow from news (which has 12 free quota); it must go to another day
    slot_ai = pick_slot(
        db=db_session,
        destination=dest,
        job=ai_job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=slots_today,
    )
    assert slot_ai.date() > today_date


def test_race_day_cap_still_three(db_session: Session, monkeypatch):
    """4. Race day (effective target doubled) -> the cap is still 3."""
    tz = "UTC"
    today_date = date(2026, 10, 26)
    now_utc = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    # Monkeypatch effective_daily_target to simulate race day boost (doubled from 20 to 40)
    monkeypatch.setattr("app.services.mode_quota.effective_daily_target", lambda *args, **kwargs: 40)

    project = TargetFanpage(
        name="Race Day Cap Fanpage",
        timezone=tz,
        is_active=True,
        ai_video_enabled=True,
        mode2_news_content_enabled=True,
        ai_video_daily_count=3,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Race Dest",
        mode="repliz",
        timezone=tz,
        daily_target=20,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"news": 60, "ai_video": 40},
        borrow_hours_before_sleep=3,
    )
    db_session.add(dest)
    db_session.flush()

    slots_today = [
        datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc),
        datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc),
    ]
    for s in slots_today:
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            created_at=now_utc.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="scheduled",
            scheduled_for=s.replace(tzinfo=None),
        )
        db_session.add(deliv)
    db_session.flush()

    ai_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ai_video,
        created_at=now_utc.replace(tzinfo=None),
    )
    db_session.add(ai_job)
    db_session.flush()

    # With 3 reservations already, next slot must go to a later day (not doubled to 6)
    slot_ai = pick_slot(
        db=db_session,
        destination=dest,
        job=ai_job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=slots_today,
    )
    assert slot_ai.date() > today_date

    # Setup a second destination with only 2 reservations on race day -> slot lands today
    dest_under = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Race Dest Under",
        mode="repliz",
        timezone=tz,
        daily_target=20,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"news": 60, "ai_video": 40},
        borrow_hours_before_sleep=3,
    )
    db_session.add(dest_under)
    db_session.flush()

    for s in slots_today[:2]:
        job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            created_at=now_utc.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            destination_id=dest_under.id,
            publish_job_id=job.id,
            state="scheduled",
            scheduled_for=s.replace(tzinfo=None),
        )
        db_session.add(deliv)
    db_session.flush()

    slot_ai_under = pick_slot(
        db=db_session,
        destination=dest_under,
        job=ai_job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=slots_today[:2],
    )
    assert slot_ai_under.date() == today_date


def test_ai_video_daily_count_none_or_zero_no_cap(db_session: Session):
    """5. ai_video_daily_count None or 0 -> old behaviour (no cap)."""
    tz = "UTC"
    today_date = date(2026, 10, 26)
    now_utc = datetime(2026, 10, 26, 10, 0, tzinfo=timezone.utc)

    for val in (None, 0):
        project = TargetFanpage(
            name=f"No Cap Fanpage {val}",
            timezone=tz,
            is_active=True,
            ai_video_enabled=True,
            mode2_news_content_enabled=True,
        )
        db_session.add(project)
        db_session.flush()

        # Plain path (mode_shares=None)
        dest_plain = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name=f"Dest Plain {val}",
            mode="repliz",
            timezone=tz,
            daily_target=10,
            min_gap_minutes=5,
            is_active=True,
            mode_shares=None,
        )
        db_session.add(dest_plain)
        db_session.flush()

        slots_today = [
            datetime(2026, 10, 26, 6, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc),
            datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc),
        ]
        for s in slots_today:
            job = PublishJob(
                fanpage_id=project.id,
                content_type=ContentType.ai_video,
                created_at=now_utc.replace(tzinfo=None),
                is_deleted=False,
            )
            db_session.add(job)
            db_session.flush()
            deliv = PublishDelivery(
                destination_id=dest_plain.id,
                publish_job_id=job.id,
                state="scheduled",
                scheduled_for=s.replace(tzinfo=None),
            )
            db_session.add(deliv)
        db_session.flush()

        ai_job = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ai_video,
            created_at=now_utc.replace(tzinfo=None),
        )
        db_session.add(ai_job)
        db_session.flush()

        # Quota path (mode_shares={"news": 60, "ai_video": 40}, daily_target=20 -> ai_video quota 8)
        dest_quota = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name=f"Dest Quota {val}",
            mode="repliz",
            timezone=tz,
            daily_target=20,
            min_gap_minutes=5,
            is_active=True,
            mode_shares={"news": 60, "ai_video": 40},
            borrow_hours_before_sleep=3,
        )
        db_session.add(dest_quota)
        db_session.flush()

        for s in slots_today:
            job = PublishJob(
                fanpage_id=project.id,
                content_type=ContentType.ai_video,
                created_at=now_utc.replace(tzinfo=None),
                is_deleted=False,
            )
            db_session.add(job)
            db_session.flush()
            deliv = PublishDelivery(
                destination_id=dest_quota.id,
                publish_job_id=job.id,
                state="scheduled",
                scheduled_for=s.replace(tzinfo=None),
            )
            db_session.add(deliv)
        db_session.flush()

        # Plain path: without cap, 4th slot lands today (3 < daily_target of 10)
        # Quota path: without cap, ai_video quota is 8, so 4th slot lands today (3 < 8)
        with db_session.no_autoflush:
            project.ai_video_daily_count = val
            slot_plain = pick_slot(
                db=db_session,
                destination=dest_plain,
                job=ai_job,
                fanpage=project,
                now_utc=now_utc,
                all_reservations=slots_today,
            )
            assert slot_plain.date() == today_date

            slot_quota = pick_slot(
                db=db_session,
                destination=dest_quota,
                job=ai_job,
                fanpage=project,
                now_utc=now_utc,
                all_reservations=slots_today,
            )
            assert slot_quota.date() == today_date
            project.ai_video_daily_count = 1
