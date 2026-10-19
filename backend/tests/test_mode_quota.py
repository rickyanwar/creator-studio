from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import zoneinfo
import pytest
from pydantic import ValidationError
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
from app.schemas.social_brands import BrandSummaryOut, ModeBacklogOut
from app.schemas.social_media import (
    SocialDestinationCreate,
    SocialDestinationOut,
    SocialDestinationUpdate,
)
from app.services.content_groups import (
    GROUP_IDS,
    GROUP_LABELS,
    enabled_groups,
    group_of,
)
from app.services.mode_quota import (
    borrow_window,
    even_shares,
    group_quotas,
    group_reservations,
    pick_slot,
    waiting_groups,
)
from app.services.publishing_schedule import compute_audience_slot
from app.services.social_delivery_planner import ensure_deliveries


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


# ── Step 1: group_quotas tests ────────────────────────────────────────────────


def test_group_quotas_spec_examples():
    """Test required examples from spec."""
    # shares None or {} -> {} (mode split is OFF)
    assert group_quotas(4, None, ["ig", "news"]) == {}
    assert group_quotas(4, {}, ["ig", "news"]) == {}

    # shares {"ig": 50, "news": 50} with 4/day -> {"ig": 2, "news": 2}
    assert group_quotas(4, {"ig": 50, "news": 50}) == {"ig": 2, "news": 2}

    # (10, {"ig": 60, "news": 40}) -> {"ig": 6, "news": 4}
    assert group_quotas(10, {"ig": 60, "news": 40}) == {"ig": 6, "news": 4}

    # (5, {"ig": 50, "news": 50}) -> {"ig": 3, "news": 2} (remainder tie-break by GROUP_IDS order)
    assert group_quotas(5, {"ig": 50, "news": 50}) == {"ig": 3, "news": 2}


def test_even_shares():
    """Test even_shares largest remainder percentage calculation."""
    assert even_shares(["ig", "news"]) == {"ig": 50, "news": 50}
    assert even_shares(["ig", "news", "discussion"]) == {
        "ig": 34,
        "news": 33,
        "discussion": 33,
    }
    assert even_shares([]) == {}
    assert even_shares(None) == {}


def test_group_quotas_remainder_and_zero_share():
    """Test largest remainder tie breaking and share 0 gets 0."""
    # 0 share gets 0 even if remainder exists
    quotas = group_quotas(1, {"ig": 0, "news": 100})
    assert quotas == {"ig": 0, "news": 1}

    # 3 target, 0 share on ig, news and discussion 50/50 -> news gets tie break over discussion
    quotas = group_quotas(3, {"ig": 0, "news": 50, "discussion": 50})
    assert quotas == {"ig": 0, "news": 2, "discussion": 1}


def test_group_quotas_no_enabled_no_shares():
    """With no enabled groups and no shares -> return {}."""
    assert group_quotas(4, None, []) == {}
    assert group_quotas(4, {}, []) == {}
    assert group_quotas(4, None, None) == {}


# ── Step 2: Schema validation tests ───────────────────────────────────────────


def test_schema_validation_mode_shares():
    """Unknown key -> 422, sum != 100 -> 422, {} and None -> OK."""
    # Unknown key -> raises ValidationError
    with pytest.raises(ValidationError) as exc:
        SocialDestinationCreate(
            content_project_id=1,
            kind="facebook_page",
            name="Test",
            daily_target=1,
            repliz_daily_target=1,
            mode_shares={"invalid_group": 100},
        )
    assert "Unknown group id" in str(exc.value)

    # Sum != 100 -> raises ValidationError
    with pytest.raises(ValidationError) as exc:
        SocialDestinationCreate(
            content_project_id=1,
            kind="facebook_page",
            name="Test",
            daily_target=1,
            repliz_daily_target=1,
            mode_shares={"ig": 60, "news": 30},
        )
    assert "Mode shares must sum to 100" in str(exc.value)

    # {} allowed and stored as NULL (None)
    c1 = SocialDestinationCreate(
        content_project_id=1,
        kind="facebook_page",
        name="Test",
        daily_target=1,
        repliz_daily_target=1,
        mode_shares={},
    )
    assert c1.mode_shares is None

    u_empty = SocialDestinationUpdate(mode_shares={})
    assert u_empty.mode_shares is None

    # None OK
    c2 = SocialDestinationCreate(
        content_project_id=1,
        kind="facebook_page",
        name="Test",
        daily_target=1,
        repliz_daily_target=1,
        mode_shares=None,
    )
    assert c2.mode_shares is None

    # Update schema validation
    with pytest.raises(ValidationError):
        SocialDestinationUpdate(mode_shares={"ig": 90})

    u = SocialDestinationUpdate(mode_shares={"ig": 70, "news": 30})
    assert u.mode_shares == {"ig": 70, "news": 30}


# ── Step 3: Agung case & borrowing ─────────────────────────────────────────────


def test_agung_case_five_ig_jobs(db_session: Session):
    """Agung case: 4/day, ig + news enabled, sleep 23-07.

    First 2 IG land today. 3rd IG borrows (>= 20:00) when no news is waiting.
    """
    tz = "Asia/Jakarta"
    project = TargetFanpage(
        name="Agung",
        timezone=tz,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=7,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Agung FB Page",
        mode="repliz",
        timezone=tz,
        daily_target=4,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"ig": 50, "news": 50},
        borrow_hours_before_sleep=3,
        news_stale_hours=24,
    )
    db_session.add(dest)
    db_session.flush()

    # Morning local time on 2026-10-19 (09:00 WIB = 02:00 UTC)
    t0 = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
    target_tz = zoneinfo.ZoneInfo(tz)

    jobs = []
    for i in range(5):
        j = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ig_repost,
            watermarked_image_urls=[f"https://cdn.example.com/ig_{i}.png"],
            ai_generated_caption=f"IG Post {i}",
            created_at=t0.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(j)
        jobs.append(j)
    db_session.flush()

    # Job 1 and Job 2 (within quota = 2)
    d1 = ensure_deliveries(db_session, jobs[0], now_utc=t0)[0]
    d2 = ensure_deliveries(db_session, jobs[1], now_utc=t0)[0]
    assert d1.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz).date() == datetime(2026, 10, 19).date()
    assert d2.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz).date() == datetime(2026, 10, 19).date()

    # Job 3: borrowing window >= 20:00 local today
    d3 = ensure_deliveries(db_session, jobs[2], now_utc=t0)[0]
    d3_local = d3.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz)
    assert d3_local.date() == datetime(2026, 10, 19).date()
    assert d3_local.hour >= 20
    assert d3_local.hour < 23

    # Job 4 and Job 5 roll to tomorrow since today's borrow window has no more slots
    d4 = ensure_deliveries(db_session, jobs[3], now_utc=t0)[0]
    d4_local = d4.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz)
    assert d4_local.date() == datetime(2026, 10, 20).date()


def test_waiting_news_blocks_borrowing(db_session: Session):
    """With a news job waiting, IG cannot borrow the News slots."""
    tz = "Asia/Jakarta"
    project = TargetFanpage(
        name="Agung News Block",
        timezone=tz,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=7,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Agung FB Page 2",
        mode="repliz",
        timezone=tz,
        daily_target=4,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"ig": 50, "news": 50},
        borrow_hours_before_sleep=3,
        news_stale_hours=24,
    )
    db_session.add(dest)
    db_session.flush()

    t0 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    target_tz = zoneinfo.ZoneInfo(tz)

    ig1 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://cdn.example.com/ig1.png"],
        created_at=t0.replace(tzinfo=None),
        is_deleted=False,
    )
    ig2 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://cdn.example.com/ig2.png"],
        created_at=t0.replace(tzinfo=None),
        is_deleted=False,
    )
    ig3 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://cdn.example.com/ig3.png"],
        created_at=t0.replace(tzinfo=None),
        is_deleted=False,
    )
    news_waiting = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/news.png",
        status=PublishJobStatus.pending_publish,
        created_at=t0.replace(tzinfo=None),
        is_deleted=False,
    )
    db_session.add_all([ig1, ig2, ig3, news_waiting])
    db_session.flush()

    ensure_deliveries(db_session, ig1, now_utc=t0)
    ensure_deliveries(db_session, ig2, now_utc=t0)

    # With news_waiting present (and no delivery for this destination yet),
    # ig3 cannot borrow and rolls to tomorrow
    d3 = ensure_deliveries(db_session, ig3, now_utc=t0)[0]
    d3_local = d3.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz)
    assert d3_local.date() == datetime(2026, 10, 16).date()


def test_borrow_hours_zero_never_borrows(db_session: Session):
    """When borrow_hours_before_sleep is 0, borrowing is disabled."""
    tz = "Asia/Jakarta"
    project = TargetFanpage(
        name="No Borrow Page",
        timezone=tz,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=7,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="No Borrow Dest",
        mode="repliz",
        timezone=tz,
        daily_target=4,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"ig": 50, "news": 50},
        borrow_hours_before_sleep=0,
    )
    db_session.add(dest)
    db_session.flush()

    t0 = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
    target_tz = zoneinfo.ZoneInfo(tz)

    jobs = [
        PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ig_repost,
            watermarked_image_urls=[f"https://cdn.example.com/ig_{i}.png"],
            created_at=t0.replace(tzinfo=None),
            is_deleted=False,
        )
        for i in range(3)
    ]
    db_session.add_all(jobs)
    db_session.flush()

    ensure_deliveries(db_session, jobs[0], now_utc=t0)
    ensure_deliveries(db_session, jobs[1], now_utc=t0)

    # 3rd job cannot borrow since borrow_hours is 0 -> rolls to tomorrow
    d3 = ensure_deliveries(db_session, jobs[2], now_utc=t0)[0]
    d3_local = d3.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz)
    assert d3_local.date() == datetime(2026, 10, 16).date()


# ── Step 4: Stale news test ───────────────────────────────────────────────────


def test_stale_news_skipped_with_error(db_session: Session):
    """News job whose earliest slot is > news_stale_hours is marked skipped."""
    tz = "UTC"
    project = TargetFanpage(
        name="News Stale Test",
        timezone=tz,
        is_active=True,
        mode2_news_content_enabled=True,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="News Dest",
        mode="repliz",
        timezone=tz,
        daily_target=1,
        min_gap_minutes=5,
        is_active=True,
        news_stale_hours=24,
    )
    db_session.add(dest)
    db_session.flush()

    # Job created 25 hours ago
    now_utc = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
    created_at = now_utc - timedelta(hours=25)

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/news.png",
        created_at=created_at.replace(tzinfo=None),
    )
    db_session.add(job)
    db_session.flush()

    deliveries = ensure_deliveries(db_session, job, now_utc=now_utc)
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.state == "skipped"
    assert d.scheduled_for is None
    assert d.last_error == "stale: no News slot within 24 h"

    # Confirm it did NOT reserve a slot: get_destination_reservations_utc is empty
    from app.services.publishing_schedule import get_destination_reservations_utc

    res = get_destination_reservations_utc(db_session, dest)
    assert len(res) == 0


# ── Step 5: Regression test (no enabled groups) ───────────────────────────────


def test_quotas_empty_identical_to_compute_audience_slot(db_session: Session):
    """When no groups are enabled, pick_slot falls back to compute_audience_slot."""
    project = TargetFanpage(
        name="No Modes Fanpage",
        timezone="UTC",
        is_active=True,
        mode1_ig_repost_enabled=False,
        mode2_news_content_enabled=False,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Plain Dest",
        mode="repliz",
        timezone="UTC",
        daily_target=3,
        min_gap_minutes=10,
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        design_image_url="https://cdn.example.com/plain.png",
        created_at=now_utc.replace(tzinfo=None),
    )
    db_session.add(job)
    db_session.flush()

    old_slot = compute_audience_slot(
        now_utc=now_utc,
        timezone_name=dest.timezone,
        daily_target=dest.daily_target,
        min_gap_minutes=dest.min_gap_minutes,
        existing_slots_utc=[],
        namespace=f"dest-{dest.id}",
    )

    new_slot = pick_slot(
        db=db_session,
        destination=dest,
        job=job,
        fanpage=project,
        now_utc=now_utc,
        all_reservations=[],
    )

    assert new_slot == old_slot


def test_channel_without_shares_plain_slot_regression(db_session: Session):
    """When groups are enabled but destination has mode_shares=None, keep plain first-come slots."""
    tz = "Asia/Jakarta"
    target_tz = zoneinfo.ZoneInfo(tz)
    project = TargetFanpage(
        name="Plain Slot Fanpage",
        timezone=tz,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=7,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="No Shares Dest",
        mode="repliz",
        timezone=tz,
        daily_target=4,
        min_gap_minutes=5,
        is_active=True,
        mode_shares=None,
        borrow_hours_before_sleep=3,
        news_stale_hours=24,
    )
    db_session.add(dest)
    db_session.flush()

    t0 = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
    jobs = [
        PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ig_repost,
            watermarked_image_urls=[f"https://cdn.example.com/ig_{i}.png"],
            ai_generated_caption=f"IG Post {i}",
            created_at=t0.replace(tzinfo=None),
            is_deleted=False,
        )
        for i in range(4)
    ]
    db_session.add_all(jobs)
    db_session.flush()

    # All 4 IG jobs should land on today (2026-10-19) since mode split is OFF
    deliveries = []
    for j in jobs:
        d = ensure_deliveries(db_session, j, now_utc=t0)[0]
        deliveries.append(d)
        d_local = d.scheduled_for.replace(tzinfo=timezone.utc).astimezone(target_tz)
        assert d_local.date() == datetime(2026, 10, 19).date()

    # Regression: pick_slot directly matches compute_audience_slot
    test_job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        created_at=t0.replace(tzinfo=None),
    )
    existing_all = [
        d.scheduled_for.replace(tzinfo=timezone.utc)
        for d in deliveries
    ]
    plain_slot = compute_audience_slot(
        now_utc=t0,
        timezone_name=dest.timezone,
        daily_target=dest.daily_target,
        min_gap_minutes=dest.min_gap_minutes,
        existing_slots_utc=existing_all,
        sleep_start_hour=23,
        sleep_end_hour=7,
        namespace=f"dest-{dest.id}",
    )
    quota_slot = pick_slot(
        db=db_session,
        destination=dest,
        job=test_job,
        fanpage=project,
        now_utc=t0,
        all_reservations=existing_all,
        sleep_start=23,
        sleep_end=7,
    )
    assert quota_slot == plain_slot


# ── Step 6: Brand summary backlog test ────────────────────────────────────────


def test_brand_summary_backlog_fields(db_session: Session):
    """Verify enabled_groups and mode_backlog in brand summary."""
    from app.api.deps import CurrentUser
    from app.api.social_brands import _build_brand_summary
    from app.models.settings import Settings

    project = TargetFanpage(
        name="Backlog Project",
        timezone="Asia/Jakarta",
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Backlog Dest",
        mode="repliz",
        timezone="Asia/Jakarta",
        daily_target=4,
        is_active=True,
        borrow_hours_before_sleep=3,
    )
    db_session.add(dest)
    db_session.flush()

    now_utc = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)
    # Add 4 waiting IG jobs
    for i in range(4):
        j = PublishJob(
            fanpage_id=project.id,
            content_type=ContentType.ig_repost,
            status=PublishJobStatus.pending_publish,
            created_at=now_utc.replace(tzinfo=None),
            is_deleted=False,
        )
        db_session.add(j)
    db_session.flush()

    settings = Settings()
    summary = _build_brand_summary(db_session, project, settings, now_utc)

    assert summary.enabled_groups == ["ig", "news"]
    # mode_backlog includes ig and news
    backlog_by_group = {b.group: b for b in summary.mode_backlog}
    assert "ig" in backlog_by_group
    assert "news" in backlog_by_group

    ig_b = backlog_by_group["ig"]
    assert ig_b.waiting == 4
    assert ig_b.per_day == 2
    assert ig_b.days == 2.0  # 4 / 2

    news_b = backlog_by_group["news"]
    assert news_b.waiting == 0
    assert news_b.per_day == 2
    assert news_b.days == 0.0

    # With explicit shares on destination: uses destination shares quota directly
    dest.mode_shares = {"ig": 75, "news": 25}
    db_session.flush()
    summary2 = _build_brand_summary(db_session, project, settings, now_utc)
    backlog2 = {b.group: b for b in summary2.mode_backlog}
    assert backlog2["ig"].per_day == 3
    assert backlog2["news"].per_day == 1


# ── Step 7: borrow_window tests for real production windows ────────────────────


def test_borrow_window_cases():
    """Verify borrow window start and end hours for production sleep profiles."""
    from datetime import date
    tz = zoneinfo.ZoneInfo("Asia/Jakarta")
    d = date(2026, 10, 15)

    # 1. No sleep window (Agung): 3h before midnight -> [21:00, 00:00)
    w_start, w_end = borrow_window(d, None, None, 3, tz)
    assert w_start.astimezone(tz) == datetime(2026, 10, 15, 21, 0, tzinfo=tz)
    assert w_end.astimezone(tz) == datetime(2026, 10, 16, 0, 0, tzinfo=tz)

    # 2. 22-8 (Formula Zone / Formula Weekend / intan / Archives): [19:00, 22:00)
    w_start, w_end = borrow_window(d, 22, 8, 3, tz)
    assert w_start.astimezone(tz) == datetime(2026, 10, 15, 19, 0, tzinfo=tz)
    assert w_end.astimezone(tz) == datetime(2026, 10, 15, 22, 0, tzinfo=tz)

    # 3. 23-9 (GP Weekend): [20:00, 23:00)
    w_start, w_end = borrow_window(d, 23, 9, 3, tz)
    assert w_start.astimezone(tz) == datetime(2026, 10, 15, 20, 0, tzinfo=tz)
    assert w_end.astimezone(tz) == datetime(2026, 10, 15, 23, 0, tzinfo=tz)

    # 4. 1-9 (Fight Today & GP Racing): sleep begins after midnight -> [22:00, 00:00)
    w_start, w_end = borrow_window(d, 1, 9, 3, tz)
    assert w_start.astimezone(tz) == datetime(2026, 10, 15, 22, 0, tzinfo=tz)
    assert w_end.astimezone(tz) == datetime(2026, 10, 16, 0, 0, tzinfo=tz)

    # 5. 0-7: sleep begins at midnight -> [21:00, 00:00)
    w_start, w_end = borrow_window(d, 0, 7, 3, tz)
    assert w_start.astimezone(tz) == datetime(2026, 10, 15, 21, 0, tzinfo=tz)
    assert w_end.astimezone(tz) == datetime(2026, 10, 16, 0, 0, tzinfo=tz)


def test_sleep_1_9_does_not_borrow_at_15_local(db_session: Session):
    """With sleep 1-9, an IG job at 15:00 local does NOT borrow into daytime slots."""
    tz = "Asia/Jakarta"
    target_tz = zoneinfo.ZoneInfo(tz)
    project = TargetFanpage(
        name="Fight Today",
        timezone=tz,
        is_active=True,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=True,
        publish_sleep_start_hour=1,
        publish_sleep_end_hour=9,
    )
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Fight Today FB",
        mode="repliz",
        timezone=tz,
        daily_target=4,
        min_gap_minutes=5,
        is_active=True,
        mode_shares={"ig": 50, "news": 50},
        borrow_hours_before_sleep=3,
        news_stale_hours=24,
    )
    db_session.add(dest)
    db_session.flush()

    # Daytime 15:00 local (= 08:00 UTC) on 2026-10-19
    t_15 = datetime(2026, 10, 19, 8, 0, tzinfo=timezone.utc)

    # Pre-occupy 2 IG slots on 2026-10-19
    ig1 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://cdn.example.com/ig1.png"],
        created_at=t_15.replace(tzinfo=None),
        is_deleted=False,
    )
    ig2 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://cdn.example.com/ig2.png"],
        created_at=t_15.replace(tzinfo=None),
        is_deleted=False,
    )
    db_session.add_all([ig1, ig2])
    db_session.flush()

    ensure_deliveries(db_session, ig1, now_utc=t_15)
    ensure_deliveries(db_session, ig2, now_utc=t_15)

    # 3rd IG job arrives at 15:00 local
    ig3 = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        watermarked_image_urls=["https://cdn.example.com/ig3.png"],
        created_at=t_15.replace(tzinfo=None),
        is_deleted=False,
    )
    db_session.add(ig3)
    db_session.flush()

    all_reservations = [
        d.scheduled_for.replace(tzinfo=timezone.utc)
        for d in db_session.query(PublishDelivery).filter_by(destination_id=dest.id).all()
        if d.scheduled_for
    ]

    slot = pick_slot(
        db=db_session,
        destination=dest,
        job=ig3,
        fanpage=project,
        now_utc=t_15,
        all_reservations=all_reservations,
        sleep_start=1,
        sleep_end=9,
    )

    slot_local = slot.astimezone(target_tz)
    # The slot MUST NOT be at 15:xx local. For 1-9 sleep with 3h borrow, window is [22:00, 00:00).
    # If on day 2026-10-19, it must be >= 22:00. Otherwise, it rolled to tomorrow.
    if slot_local.date() == datetime(2026, 10, 19).date():
        assert slot_local.hour >= 22
    else:
        assert slot_local.date() > datetime(2026, 10, 19).date()
