from __future__ import annotations

from datetime import datetime, timezone
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
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.mode_quota import (
    borrow_window,
    group_quotas,
    pick_slot,
)


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


# ── helpers ────────────────────────────────────────────────────────────────────

TZ = "Asia/Jakarta"          # UTC+7
TARGET_TZ = zoneinfo.ZoneInfo(TZ)
# 2026-10-19 09:00 WIB = 02:00 UTC  — morning, well before any borrow window
T0 = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
LOCAL_DATE = T0.astimezone(TARGET_TZ).date()


def _make_project(db: Session, *, name: str = "TestProject", **overrides) -> TargetFanpage:
    defaults = dict(
        name=name,
        timezone=TZ,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=7,
    )
    defaults.update(overrides)
    fp = TargetFanpage(**defaults)
    db.add(fp)
    db.flush()
    return fp


def _make_dest(db: Session, project: TargetFanpage, *, name: str = "TestDest", **overrides) -> SocialDestination:
    defaults = dict(
        content_project_id=project.id,
        kind="facebook_page",
        name=name,
        mode="repliz",
        timezone=TZ,
        daily_target=18,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"ig": 50, "news": 50},
        borrow_hours_before_sleep=3,
        news_stale_hours=24,
    )
    defaults.update(overrides)
    d = SocialDestination(**defaults)
    db.add(d)
    db.flush()
    return d


def _make_job(db: Session, project: TargetFanpage, content_type: ContentType, **kw) -> PublishJob:
    defaults = dict(
        fanpage_id=project.id,
        content_type=content_type,
        created_at=T0.replace(tzinfo=None),
        is_deleted=False,
    )
    if content_type in (ContentType.ig_repost, ContentType.ig_recreate):
        defaults["watermarked_image_urls"] = ["https://cdn.example.com/img.png"]
    elif content_type == ContentType.news_content:
        defaults["design_image_url"] = "https://cdn.example.com/news.png"
    defaults.update(kw)
    j = PublishJob(**defaults)
    db.add(j)
    db.flush()
    return j


def _add_delivery(db: Session, dest: SocialDestination, job: PublishJob, scheduled_for: datetime) -> PublishDelivery:
    d = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=job.id,
        state="scheduled",
        scheduled_for=scheduled_for.replace(tzinfo=None),
    )
    db.add(d)
    db.flush()
    return d


def _reservations(db: Session, dest: SocialDestination) -> list[datetime]:
    rows = db.query(PublishDelivery).filter_by(destination_id=dest.id).all()
    return [r.scheduled_for.replace(tzinfo=timezone.utc) for r in rows if r.scheduled_for]


# ── Case 1: news borrows BEFORE the borrow window ─────────────────────────────


def test_news_borrows_before_window(db_session: Session):
    """News quota full, ig has free slots, no ig waiting -> news slot is on day d
    and EARLIER than borrow_window start."""
    fp = _make_project(db_session)
    dest = _make_dest(db_session, fp)

    # quotas: ig 9, news 9 (daily_target=18, 50/50)
    q = group_quotas(18, {"ig": 50, "news": 50})
    assert q == {"ig": 9, "news": 9}

    # Fill 9 news deliveries on LOCAL_DATE to exhaust news quota
    for i in range(9):
        nj = _make_job(db_session, fp, ContentType.news_content)
        hour_utc = 3 + i  # spread through the day
        _add_delivery(db_session, dest, nj, datetime(2026, 10, 19, hour_utc, 0, tzinfo=timezone.utc))

    # The 10th news job needs to borrow
    news_job = _make_job(db_session, fp, ContentType.news_content)
    reservations = _reservations(db_session, dest)

    slot = pick_slot(
        db=db_session,
        destination=dest,
        job=news_job,
        fanpage=fp,
        now_utc=T0,
        all_reservations=reservations,
        sleep_start=23,
        sleep_end=7,
    )

    slot_local = slot.astimezone(TARGET_TZ)
    w_start, _ = borrow_window(LOCAL_DATE, 23, 7, 3, TARGET_TZ)
    w_start_local = w_start.astimezone(TARGET_TZ)

    # Must be on the same day AND before the borrow window
    assert slot_local.date() == LOCAL_DATE
    assert slot < w_start, (
        f"news slot {slot_local} should be earlier than borrow window start {w_start_local}"
    )


# ── Case 2: ig waiting blocks news from borrowing before window ────────────────


def test_news_cannot_borrow_when_ig_waiting(db_session: Session):
    """Same as case 1 but an ig PublishJob is waiting -> news slot is NOT before
    the borrow window start on day d (old behaviour)."""
    fp = _make_project(db_session)
    dest = _make_dest(db_session, fp)

    # Fill 9 news deliveries
    for i in range(9):
        nj = _make_job(db_session, fp, ContentType.news_content)
        _add_delivery(db_session, dest, nj, datetime(2026, 10, 19, 3 + i, 0, tzinfo=timezone.utc))

    # Create a waiting IG job (pending_publish, created within 24h, no delivery for this dest)
    _make_job(
        db_session, fp, ContentType.ig_repost,
        status=PublishJobStatus.pending_publish,
    )

    news_job = _make_job(db_session, fp, ContentType.news_content)
    reservations = _reservations(db_session, dest)

    slot = pick_slot(
        db=db_session,
        destination=dest,
        job=news_job,
        fanpage=fp,
        now_utc=T0,
        all_reservations=reservations,
        sleep_start=23,
        sleep_end=7,
    )

    slot_local = slot.astimezone(TARGET_TZ)
    w_start, _ = borrow_window(LOCAL_DATE, 23, 7, 3, TARGET_TZ)

    assert slot_local.date() > LOCAL_DATE or slot >= w_start


# ── Case 3: non-news unchanged — ig only borrows inside window ─────────────────


def test_ig_still_only_borrows_in_window(db_session: Session):
    """ig quota full, news has free slots, no news waiting -> ig slot is still
    only inside borrow window on day d (or later day), never earlier."""
    fp = _make_project(db_session)
    dest = _make_dest(db_session, fp)

    # Fill 9 ig deliveries
    for i in range(9):
        ij = _make_job(db_session, fp, ContentType.ig_repost)
        _add_delivery(db_session, dest, ij, datetime(2026, 10, 19, 3 + i, 0, tzinfo=timezone.utc))

    ig_job = _make_job(db_session, fp, ContentType.ig_repost)
    reservations = _reservations(db_session, dest)

    slot = pick_slot(
        db=db_session,
        destination=dest,
        job=ig_job,
        fanpage=fp,
        now_utc=T0,
        all_reservations=reservations,
        sleep_start=23,
        sleep_end=7,
    )

    slot_local = slot.astimezone(TARGET_TZ)
    w_start, _ = borrow_window(LOCAL_DATE, 23, 7, 3, TARGET_TZ)

    assert slot_local.date() > LOCAL_DATE or slot >= w_start


# ── Case 4: news borrows exactly 1 slot from ai_video overflow ─────────────────


def test_news_borrows_one_slot_from_ai_video_overflow(db_session: Session):
    """ai_video quota 4, ai_video_daily_count=3, one ai_video waiting, news quota full,
    no ig share -> news may borrow exactly 1 slot on day d at any time."""
    fp = _make_project(
        db_session,
        name="AIVideoOverflow",
        ai_video_enabled=True,
        ai_video_daily_count=3,
    )
    # Only news + ai_video enabled, no ig: shares must sum to 100
    dest = _make_dest(
        db_session, fp,
        name="AVDest",
        daily_target=10,
        mode_shares={"news": 60, "ai_video": 40},
    )

    # Verify quotas: news=6, ai_video=4
    q = group_quotas(10, {"news": 60, "ai_video": 40})
    assert q == {"news": 6, "ai_video": 4}

    # Fill 6 news deliveries
    for i in range(6):
        nj = _make_job(db_session, fp, ContentType.news_content)
        _add_delivery(db_session, dest, nj, datetime(2026, 10, 19, 3 + i, 0, tzinfo=timezone.utc))

    # Create a waiting ai_video job (pending_design, no delivery for this dest)
    _make_job(
        db_session, fp, ContentType.ai_video,
        status=PublishJobStatus.pending_design,
    )

    # First news borrow attempt: should get day-d slot before window
    news1 = _make_job(db_session, fp, ContentType.news_content)
    reservations = _reservations(db_session, dest)

    slot1 = pick_slot(
        db=db_session,
        destination=dest,
        job=news1,
        fanpage=fp,
        now_utc=T0,
        all_reservations=reservations,
        sleep_start=23,
        sleep_end=7,
    )

    slot1_local = slot1.astimezone(TARGET_TZ)
    w_start, _ = borrow_window(LOCAL_DATE, 23, 7, 3, TARGET_TZ)

    # Exactly 1 borrowable slot (ai_video quota 4 minus cap 3 = 1 overflow slot)
    assert slot1_local.date() == LOCAL_DATE
    assert slot1 < w_start, (
        f"first news borrow {slot1_local} should be before window"
    )

    # Record this delivery
    _add_delivery(db_session, dest, news1, slot1)

    # Second news borrow attempt: should NOT get a day-d slot before window
    news2 = _make_job(db_session, fp, ContentType.news_content)
    reservations2 = _reservations(db_session, dest)

    slot2 = pick_slot(
        db=db_session,
        destination=dest,
        job=news2,
        fanpage=fp,
        now_utc=T0,
        all_reservations=reservations2,
        sleep_start=23,
        sleep_end=7,
    )

    slot2_local = slot2.astimezone(TARGET_TZ)

    assert slot2_local.date() > LOCAL_DATE or slot2 >= w_start


# ── Case 5: borrow_hours_before_sleep=0 disables all borrowing for news ────────


def test_borrow_hours_zero_disables_news_borrowing(db_session: Session):
    """borrow_hours_before_sleep=0 with case 1 situation -> news does NOT get
    a day-d slot (borrowing fully disabled)."""
    fp = _make_project(db_session)
    dest = _make_dest(db_session, fp, borrow_hours_before_sleep=0)

    # Fill 9 news deliveries
    for i in range(9):
        nj = _make_job(db_session, fp, ContentType.news_content)
        _add_delivery(db_session, dest, nj, datetime(2026, 10, 19, 3 + i, 0, tzinfo=timezone.utc))

    news_job = _make_job(db_session, fp, ContentType.news_content)
    reservations = _reservations(db_session, dest)

    slot = pick_slot(
        db=db_session,
        destination=dest,
        job=news_job,
        fanpage=fp,
        now_utc=T0,
        all_reservations=reservations,
        sleep_start=23,
        sleep_end=7,
    )

    slot_local = slot.astimezone(TARGET_TZ)
    # Must NOT be on LOCAL_DATE — borrowing disabled
    assert slot_local.date() > LOCAL_DATE, (
        f"news slot {slot_local} should not be on {LOCAL_DATE} when borrow_hours=0"
    )
