"""Tests for TASK B — race-day detection timing and news freshness by creation day.

Verifies:
1. refresh_keyword_event_dates: passed event + checked 21 h ago → re-checked.
2. refresh: passed event + checked 2 h ago → NOT re-checked.
3. refresh: future event → NOT re-checked (unchanged).
4. refresh: None event, checked 2 days ago → NOT called; checked 6 days ago → called.
5. visual_fair_queue: yesterday's job (normal day) survives 6 h race-day cap; today's 7 h job dropped.
6. planner _plan_slot: day arg to news_fresh_hours is the job's creation day, not today.
7. dispatch _news_dispatch_gate: day arg to news_fresh_hours is the job's creation day in dest tz.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch, call
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

from app.models.design_templates import DesignTemplate
from app.models.gallery import GalleryKeyword
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    PublishDelivery,
    RecoveryIncident,
    SocialDestination,
)
from app.models.target_fanpages import PublishMode, TargetFanpage


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
    DeviceLease.__table__.create(engine)
    RecoveryIncident.__table__.create(engine)
    OperationalNotification.__table__.create(engine)
    ScrapedArticle.__table__.create(engine)
    DesignTemplate.__table__.create(engine)
    GalleryKeyword.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    session = Session(engine)
    try:
        yield session
    finally:
        session.rollback()
        session.close()


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    def get(self, key: str):
        return self.data.get(key)

    def mget(self, keys: list[str]):
        return [self.data.get(k) for k in keys]

    def incr(self, key: str, amount: int = 1):
        val = int(self.data.get(key, 0)) + amount
        self.data[key] = str(val)
        return val

    def expire(self, key: str, time: int):
        return True

    def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.data:
            return None
        self.data[key] = str(value)
        return True

    def exists(self, *keys: str) -> int:
        return sum(1 for k in keys if k in self.data)

    def delete(self, *keys: str):
        count = 0
        for k in keys:
            if self.data.pop(k, None) is not None:
                count += 1
        return count


# ---------------------------------------------------------------------------
# B1 tests: refresh_keyword_event_dates re-check interval for passed events
# ---------------------------------------------------------------------------

def _real_now_naive():
    """Current UTC as naive datetime, same as the function under test computes."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _real_today_wib():
    """Current date in WIB, same as the function under test computes."""
    return datetime.now(zoneinfo.ZoneInfo("Asia/Jakarta")).date()


def test_refresh_passed_event_checked_21h_ago_rechecks(db_session: Session, monkeypatch):
    """1. Passed event + event_date_checked_at 21 h ago → detect_next_event_date IS called."""
    now = _real_now_naive()
    today = _real_today_wib()

    kw = GalleryKeyword(
        keyword="max verstappen",
        niche="F1",
        is_active=True,
        next_event_date=today - timedelta(days=6),  # passed 6 days ago
        event_date_checked_at=now - timedelta(hours=21),
    )
    db_session.add(kw)
    db_session.commit()

    new_date = today + timedelta(days=7)
    mock_detect = MagicMock(return_value=new_date)

    monkeypatch.setattr(
        "app.services.event_calendar.detect_next_event_date", mock_detect
    )
    monkeypatch.setattr(
        "app.tasks.gallery_downloader.SessionLocal", lambda: db_session
    )

    from app.tasks.gallery_downloader import refresh_keyword_event_dates
    refresh_keyword_event_dates()

    mock_detect.assert_called_once()
    # the task closes the session it was given, which detaches kw — read it back instead of refresh()
    stored = db_session.query(GalleryKeyword).filter_by(keyword="max verstappen").one()
    assert stored.next_event_date == new_date


def test_refresh_passed_event_checked_2h_ago_skips(db_session: Session, monkeypatch):
    """2. Passed event + event_date_checked_at 2 h ago → NOT called."""
    now = _real_now_naive()
    today = _real_today_wib()

    kw = GalleryKeyword(
        keyword="lewis hamilton",
        niche="F1",
        is_active=True,
        next_event_date=today - timedelta(days=6),
        event_date_checked_at=now - timedelta(hours=2),
    )
    db_session.add(kw)
    db_session.commit()

    mock_detect = MagicMock(return_value=None)

    monkeypatch.setattr(
        "app.services.event_calendar.detect_next_event_date", mock_detect
    )
    monkeypatch.setattr(
        "app.tasks.gallery_downloader.SessionLocal", lambda: db_session
    )

    from app.tasks.gallery_downloader import refresh_keyword_event_dates
    refresh_keyword_event_dates()

    mock_detect.assert_not_called()


def test_refresh_future_event_skips(db_session: Session, monkeypatch):
    """3. Future event → NOT called (unchanged)."""
    now = _real_now_naive()
    today = _real_today_wib()

    kw = GalleryKeyword(
        keyword="charles leclerc",
        niche="F1",
        is_active=True,
        next_event_date=today + timedelta(days=3),  # future
        event_date_checked_at=now - timedelta(days=10),
    )
    db_session.add(kw)
    db_session.commit()

    mock_detect = MagicMock(return_value=None)

    monkeypatch.setattr(
        "app.services.event_calendar.detect_next_event_date", mock_detect
    )
    monkeypatch.setattr(
        "app.tasks.gallery_downloader.SessionLocal", lambda: db_session
    )

    from app.tasks.gallery_downloader import refresh_keyword_event_dates
    refresh_keyword_event_dates()

    mock_detect.assert_not_called()


def test_refresh_none_event_5day_rule_unchanged(db_session: Session, monkeypatch):
    """4. None event + checked 2 days ago → NOT called; checked 6 days ago → called."""
    now = _real_now_naive()

    kw_recent = GalleryKeyword(
        keyword="lando norris",
        niche="F1",
        is_active=True,
        next_event_date=None,
        event_date_checked_at=now - timedelta(days=2),
    )
    kw_old = GalleryKeyword(
        keyword="oscar piastri",
        niche="F1",
        is_active=True,
        next_event_date=None,
        event_date_checked_at=now - timedelta(days=6),
    )
    db_session.add_all([kw_recent, kw_old])
    db_session.commit()

    detected_calls: list[str] = []

    def fake_detect(db, keyword, niche, allow_search_fallback=False):
        detected_calls.append(keyword)
        return None

    monkeypatch.setattr(
        "app.services.event_calendar.detect_next_event_date", fake_detect
    )
    monkeypatch.setattr(
        "app.tasks.gallery_downloader.SessionLocal", lambda: db_session
    )

    from app.tasks.gallery_downloader import refresh_keyword_event_dates
    refresh_keyword_event_dates()

    assert "lando norris" not in detected_calls
    assert "oscar piastri" in detected_calls


def test_refresh_passed_event_20d_ago_checked_21h_ago_skips(db_session: Session, monkeypatch):
    """Off-season: passed date 20 days ago, checked 21 h ago → detection NOT called."""
    now = _real_now_naive()
    today = _real_today_wib()

    kw = GalleryKeyword(
        keyword="carlos sainz",
        niche="F1",
        is_active=True,
        next_event_date=today - timedelta(days=20),
        event_date_checked_at=now - timedelta(hours=21),
    )
    db_session.add(kw)
    db_session.commit()

    mock_detect = MagicMock(return_value=None)
    monkeypatch.setattr(
        "app.services.event_calendar.detect_next_event_date", mock_detect
    )
    monkeypatch.setattr(
        "app.tasks.gallery_downloader.SessionLocal", lambda: db_session
    )

    from app.tasks.gallery_downloader import refresh_keyword_event_dates
    refresh_keyword_event_dates()

    mock_detect.assert_not_called()


def test_refresh_passed_event_20d_ago_checked_6d_ago_rechecks(db_session: Session, monkeypatch):
    """Off-season: passed date 20 days ago, checked 6 days ago → detection called."""
    now = _real_now_naive()
    today = _real_today_wib()

    kw = GalleryKeyword(
        keyword="george russell",
        niche="F1",
        is_active=True,
        next_event_date=today - timedelta(days=20),
        event_date_checked_at=now - timedelta(days=6),
    )
    db_session.add(kw)
    db_session.commit()

    new_date = today + timedelta(days=30)
    mock_detect = MagicMock(return_value=new_date)
    monkeypatch.setattr(
        "app.services.event_calendar.detect_next_event_date", mock_detect
    )
    monkeypatch.setattr(
        "app.tasks.gallery_downloader.SessionLocal", lambda: db_session
    )

    from app.tasks.gallery_downloader import refresh_keyword_event_dates
    refresh_keyword_event_dates()

    mock_detect.assert_called_once()
    stored = db_session.query(GalleryKeyword).filter_by(keyword="george russell").one()
    assert stored.next_event_date == new_date


# ---------------------------------------------------------------------------
# B2 test 5: visual_fair_queue freshness uses job's creation day
# ---------------------------------------------------------------------------

def test_visual_queue_freshness_by_created_day(db_session: Session, monkeypatch):
    """5. Today is a race day (6 h). Job created yesterday (normal, 12 h) 8 h ago
    survives; job created today 7 h ago is dropped."""
    # Race day = today only (multiplier returns 2 for today, 1 for yesterday)
    today_wib = date(2026, 11, 1)  # Sunday race day
    yesterday_wib = date(2026, 10, 31)  # Saturday normal day

    def fake_multiplier(db, fanpage, day):
        if day == today_wib:
            return 2  # race day
        return 1  # normal day

    monkeypatch.setattr("app.services.event_days.event_multiplier", fake_multiplier)

    fanpage = TargetFanpage(
        id=200,
        name="FP Freshness Day",
        mode2_publish_mode=PublishMode.auto,
        is_active=True,
    )
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Freshness Day",
        mode="repliz",
        timezone="Asia/Jakarta",
        daily_target=3,
        news_stale_hours=24,  # base, will be capped to 6 or 12 by news_fresh_hours
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # now = 2026-11-01 05:00 UTC = 2026-11-01 12:00 WIB
    now_utc = datetime(2026, 11, 1, 5, 0, tzinfo=timezone.utc)

    # Job A: created yesterday at 21:00 UTC (04:00 WIB on Nov 1 minus 8h = yesterday 21:00 UTC
    # = Oct 31 04:00 WIB next day... let's be precise)
    # We want: created 8 h before now_utc in UTC, but on YESTERDAY in WIB.
    # now_utc = 05:00 UTC = 12:00 WIB on Nov 1
    # 8 h before = 21:00 UTC Oct 31 = 04:00 WIB Nov 1... that's today in WIB.
    # We need the job to be yesterday in WIB. WIB = UTC+7.
    # yesterday WIB means created_at in WIB < Nov 1 00:00 WIB = Oct 31 17:00 UTC
    # So created_at must be before Oct 31 17:00 UTC.
    # Let's say created Oct 31 16:00 UTC = Oct 31 23:00 WIB (yesterday WIB).
    # Age = Nov 1 05:00 UTC - Oct 31 16:00 UTC = 13 h.
    # That's > 12 h. We need it within 12 h.
    # Let's say created Oct 31 18:00 UTC = Nov 1 01:00 WIB (today WIB).
    # That's today in WIB, not yesterday. Hmm.
    #
    # To get a job that's on yesterday in WIB AND within 12 h of now:
    # yesterday in WIB → created_at_wib < 2026-11-01 00:00 WIB = 2026-10-31 17:00 UTC
    # within 12 h of now (05:00 UTC Nov 1) → created_at > Oct 31 17:00 UTC
    # These overlap at exactly 17:00 UTC. Let's say created Oct 31 16:59 UTC =
    # Oct 31 23:59 WIB (yesterday). Age = 12h01m → just over 12h.
    # We need within 12 h, so let's try 8 h before now and adjust the dates.
    #
    # Alternative approach: shift now to a time where 8h ago is still yesterday in WIB.
    # now_utc = 2026-11-01 00:00 UTC = 2026-11-01 07:00 WIB
    # 8h before = Oct 31 16:00 UTC = Oct 31 23:00 WIB (yesterday WIB) ✓
    # today_wib = Nov 1, yesterday_wib = Oct 31 ✓
    now_utc = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    # now in WIB = Nov 1 07:00 WIB

    # Job A: created 8 h ago → Oct 31 16:00 UTC = Oct 31 23:00 WIB (yesterday)
    # Yesterday is normal day → 12 h freshness. Age = 8 h < 12 h → survives.
    job_yesterday = PublishJob(
        id=2001,
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 31, 16, 0, 0),  # naive UTC
        status=PublishJobStatus.pending_design,
        is_breaking=False,
    )

    # Job B: created 7 h ago → Oct 31 17:00 UTC = Nov 1 00:00 WIB (today)
    # Today is race day → 6 h freshness. Age = 7 h > 6 h → dropped.
    job_today = PublishJob(
        id=2002,
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 31, 17, 0, 0),  # naive UTC = Nov 1 00:00 WIB
        status=PublishJobStatus.pending_design,
        is_breaking=False,
    )

    db_session.add_all([job_yesterday, job_today])
    db_session.commit()

    redis = FakeRedis()

    from app.services.visual_fair_queue import pick_next_news_job

    with patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue._next_slot", return_value=now_utc + timedelta(minutes=20)), \
         patch("app.services.visual_fair_queue._has_news_slot", return_value=True):
        picked = pick_next_news_job(db_session, redis, now_utc)

    # Job B (today, 7 h, race day 6 h cap) → skipped
    db_session.refresh(job_today)
    assert job_today.status == PublishJobStatus.skipped
    assert job_today.last_error == "stale: news older than 6 h, not rendered"

    # Job A (yesterday, 8 h, normal day 12 h cap) → survives, gets picked
    db_session.refresh(job_yesterday)
    assert job_yesterday.status == PublishJobStatus.pending_design  # not skipped
    assert picked is not None
    assert picked.id == job_yesterday.id


# ---------------------------------------------------------------------------
# B2 test 6: planner _plan_slot uses job's creation day for freshness
# ---------------------------------------------------------------------------

def test_planner_freshness_uses_created_day(db_session: Session, monkeypatch):
    """6. _plan_slot: the day argument passed to news_fresh_hours is the job's
    creation day, not today. So a job created on a normal day gets 12 h even
    when today is a race day."""
    from app.services.social_delivery_planner import _plan_slot

    recorded_days: list[date] = []
    _original_nfh = None

    # Import the real function to wrap it
    from app.services.event_days import news_fresh_hours as real_nfh

    def recording_nfh(db, fanpage, base, day):
        recorded_days.append(day)
        return real_nfh(db, fanpage, base, day)

    monkeypatch.setattr("app.services.social_delivery_planner.news_fresh_hours", recording_nfh)

    # Race day today
    today = date(2026, 11, 1)
    yesterday = date(2026, 10, 31)

    def fake_multiplier(db, fanpage, day):
        if day == today:
            return 2
        return 1

    monkeypatch.setattr("app.services.event_days.event_multiplier", fake_multiplier)

    fanpage = TargetFanpage(name="FP Plan", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Plan",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # Job created yesterday (UTC, so dest tz UTC → yesterday)
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 31, 20, 0, 0),  # yesterday UTC
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    now_utc = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

    _, _, hours, _ = _plan_slot(db_session, dest, job, now_utc, None, None)

    # The day passed to news_fresh_hours should be yesterday (job's creation day), not today
    assert len(recorded_days) >= 1
    assert recorded_days[-1] == yesterday  # job's creation day, not today
    assert hours == 12  # normal day → 12, not 6


# ---------------------------------------------------------------------------
# B2 test 7: dispatch _news_dispatch_gate uses job's creation day
# ---------------------------------------------------------------------------

def test_dispatch_freshness_uses_created_day(db_session: Session, monkeypatch):
    """7. _news_dispatch_gate: the day arg to news_fresh_hours is the job's
    creation day in dest timezone, not today."""
    from app.services.social_delivery_dispatch import _news_dispatch_gate

    recorded_days: list[date] = []
    from app.services.event_days import news_fresh_hours as real_nfh

    def recording_nfh(db, fanpage, base, day):
        recorded_days.append(day)
        return real_nfh(db, fanpage, base, day)

    monkeypatch.setattr("app.services.social_delivery_dispatch.news_fresh_hours", recording_nfh)

    today = date(2026, 11, 1)
    yesterday = date(2026, 10, 31)

    def fake_multiplier(db, fanpage, day):
        if day == today:
            return 2
        return 1

    monkeypatch.setattr("app.services.event_days.event_multiplier", fake_multiplier)

    fanpage = TargetFanpage(name="FP Dispatch Day", repliz_account_id="rep_dispatch_day", is_active=True)
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Dest Dispatch Day",
        mode="repliz",
        repliz_account_id="rep_dispatch_day",
        timezone="UTC",
        daily_target=1,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    # Job created yesterday in UTC
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        created_at=datetime(2026, 10, 31, 20, 0, 0),  # yesterday UTC
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=datetime(2026, 11, 1, 8, 10, 0),
        caption_snapshot="Test",
        media_snapshot=["https://cdn.example.com/test.png"],
        media_kind="image",
    )
    db_session.add(delivery)
    db_session.commit()

    now = datetime(2026, 11, 1, 8, 0, tzinfo=timezone.utc)

    result = _news_dispatch_gate(db_session, delivery, now)

    # The day passed to news_fresh_hours should be yesterday (job creation day in dest tz)
    assert len(recorded_days) >= 1
    assert recorded_days[-1] == yesterday
