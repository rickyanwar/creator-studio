"""Tests for news_slot_available() in social_delivery_planner.

Verifies:
- (a) no active destinations -> True
- (b) one destination with free slots today -> True
- (c) destination full / next slot > news_stale_hours -> False
- (d) two destinations, one stale and one fine -> True
- (e) pick_slot raises exception -> True (fail-open)
- (f) read-only query never calls with_for_update
- (g) non-news content types (e.g. discussion) with slot > 30h -> True
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
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
from app.services.social_delivery_planner import news_slot_available


@pytest.fixture
def db_session():
    """Create in-memory SQLite schema and session."""
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
        session.rollback()
        session.close()


def _make_fanpage(
    db: Session,
    name: str = "Test Fanpage",
    sleep_start: int | None = None,
    sleep_end: int | None = None,
) -> TargetFanpage:
    fp = TargetFanpage(
        name=name,
        repliz_account_id=f"rep_{name}",
        publish_sleep_start_hour=sleep_start,
        publish_sleep_end_hour=sleep_end,
        is_active=True,
    )
    db.add(fp)
    db.flush()
    return fp


def _make_destination(
    db: Session,
    fanpage: TargetFanpage,
    *,
    name: str = "Dest",
    is_active: bool = True,
    news_stale_hours: int = 24,
    mode: str = "repliz",
    daily_target: int = 5,
) -> SocialDestination:
    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name=name,
        mode=mode,
        timezone="UTC",
        daily_target=daily_target,
        phone_daily_target=0,
        repliz_daily_target=daily_target,
        supported_media=["image", "album", "video"],
        news_stale_hours=news_stale_hours,
        is_active=is_active,
    )
    db.add(dest)
    db.flush()
    return dest


def test_no_active_destinations_returns_true(db_session: Session):
    """Case (a): Return True when fanpage has no destinations or only inactive ones, or job has no fanpage_id."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)

    # 1. Fan page has zero destinations
    fp1 = _make_fanpage(db_session, "NoDest")
    job1 = PublishJob(
        fanpage_id=fp1.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job1)
    db_session.flush()
    assert news_slot_available(db_session, job1, now_utc=now) is True

    # 2. Fan page has only inactive destinations
    fp2 = _make_fanpage(db_session, "InactiveDest")
    _make_destination(db_session, fp2, name="Inactive", is_active=False)
    job2 = PublishJob(
        fanpage_id=fp2.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job2)
    db_session.flush()
    assert news_slot_available(db_session, job2, now_utc=now) is True

    # 3. Job with no fanpage_id
    job3 = PublishJob(
        fanpage_id=None,
        content_type=ContentType.news_content,
        created_at=now,
    )
    assert news_slot_available(db_session, job3, now_utc=now) is True


def test_one_destination_free_slots_returns_true(db_session: Session):
    """Case (b): One active destination with free slot today within stale limit returns True."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    fp = _make_fanpage(db_session, "FreeSlots")
    _make_destination(db_session, fp, name="PageDest", is_active=True)

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job)
    db_session.flush()

    assert news_slot_available(db_session, job, now_utc=now) is True


def test_destination_full_or_stale_returns_false(db_session: Session):
    """Case (c): Destination next slot > news_stale_hours after created_at returns False."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    fp = _make_fanpage(db_session, "FullDest")
    _make_destination(db_session, fp, name="PageDest", is_active=True, news_stale_hours=24)

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job)
    db_session.flush()

    with patch("app.services.mode_quota.pick_slot", return_value=now + timedelta(hours=30)):
        assert news_slot_available(db_session, job, now_utc=now) is False


def test_two_destinations_one_stale_one_fine_returns_true(db_session: Session):
    """Case (d): Two destinations, one stale and one fine returns True."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    fp = _make_fanpage(db_session, "TwoDests")
    dest1 = _make_destination(db_session, fp, name="Dest1", is_active=True)
    dest2 = _make_destination(db_session, fp, name="Dest2", is_active=True)

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job)
    db_session.flush()

    def fake_pick_slot(**kwargs):
        if kwargs["destination"].id == dest1.id:
            return now + timedelta(hours=30)  # stale
        return now + timedelta(hours=2)  # fine

    with patch("app.services.mode_quota.pick_slot", side_effect=fake_pick_slot):
        assert news_slot_available(db_session, job, now_utc=now) is True


def test_pick_slot_raises_returns_true(db_session: Session):
    """Case (e): When pick_slot raises, news_slot_available fails open and returns True."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    fp = _make_fanpage(db_session, "ErrorFP")
    _make_destination(db_session, fp, name="PageDest", is_active=True)

    job = PublishJob(
        id=42,
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job)
    db_session.flush()

    with patch("app.services.mode_quota.pick_slot", side_effect=RuntimeError("quota engine crashed")):
        assert news_slot_available(db_session, job, now_utc=now) is True


def test_never_calls_with_for_update(db_session: Session):
    """Case (f): news_slot_available is read-only and never calls with_for_update()."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    fp = _make_fanpage(db_session, "NoLockFP")
    _make_destination(db_session, fp, name="PageDest", is_active=True)

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        created_at=now,
    )
    db_session.add(job)
    db_session.flush()

    with patch("sqlalchemy.orm.Query.with_for_update") as mock_lock:
        res = news_slot_available(db_session, job, now_utc=now)
        assert res is True
        mock_lock.assert_not_called()


def test_non_news_content_type_returns_true(db_session: Session):
    """Case (g): Non-news content type (e.g. discussion) with slot > 30h returns True (cannot be stale news)."""
    now = datetime(2026, 10, 20, 10, 0, tzinfo=timezone.utc)
    fp = _make_fanpage(db_session, "DiscussionFP")
    _make_destination(db_session, fp, name="PageDest", is_active=True, news_stale_hours=24)

    job = PublishJob(
        fanpage_id=fp.id,
        content_type=ContentType.discussion,
        created_at=now,
    )
    db_session.add(job)
    db_session.flush()

    with patch("app.services.mode_quota.pick_slot", return_value=now + timedelta(hours=30)):
        assert news_slot_available(db_session, job, now_utc=now) is True
