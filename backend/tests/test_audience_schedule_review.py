"""Independent adversarial review tests for audience-aware delivery scheduling.

Tests:
1. Datetime and finite parameter validation (nan/inf/bool, hours bool/float, target > 1000, namespace).
2. Actual runtime burst prevention for <10min overdue Phone jobs (safe actual-send minGap guard).
3. Concurrency and claim safety in stale delivery replanning.
4. Remote immutability (never move published, repliz_scheduled, uncertain, or attempted rows).
5. Parent PublishJob.scheduled_for synchronization on stale replan.
6. DST and sleep window boundary invariants.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import zoneinfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
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
from app.models.target_fanpages import TargetFanpage
from app.services.publishing_schedule import (
    compute_audience_slot,
    _pick_slot_instant,
    is_legacy_repliz_eligible,
)
from app.services.social_delivery_dispatch import (
    PhonePublishResult,
    dispatch_due_deliveries,
    replan_stale_scheduled_deliveries,
)
from app.services.social_delivery_planner import ensure_deliveries


@pytest.fixture
def review_db():
    """Isolated in-memory SQLite fixture scoped strictly for review suite."""
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
    SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = None
    session = None
    try:
        engine = create_engine("sqlite:///:memory:")
        TargetFanpage.__table__.create(engine)
        SocialDestination.__table__.create(engine)
        PhoneDevice.__table__.create(engine)
        DestinationDeviceAssignment.__table__.create(engine)
        DeviceLease.__table__.create(engine)
        RecoveryIncident.__table__.create(engine)
        OperationalNotification.__table__.create(engine)
        Post.__table__.create(engine)
        PublishJob.__table__.create(engine)
        PublishDelivery.__table__.create(engine)
        DeliveryEvent.__table__.create(engine)

        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
    finally:
        if session is not None:
            session.close()
        if engine is not None:
            engine.dispose()
        sd_col.server_default = orig_sd
        de_col.server_default = orig_de


# ── 1. Datetime and Finite Input Validation ───────────────────────────────────


def test_finite_validation_rejects_nan_inf_and_bool_min_gap():
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    # bool must be rejected
    with pytest.raises(ValueError, match="min_gap_minutes"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=True,
            existing_slots_utc=[],
        )

    # NaN must be rejected
    with pytest.raises(ValueError, match="min_gap_minutes"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=float("nan"),
            existing_slots_utc=[],
        )

    # Inf must be rejected
    with pytest.raises(ValueError, match="min_gap_minutes"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=float("inf"),
            existing_slots_utc=[],
        )


def test_finite_validation_rejects_bool_and_noninteger_sleep_hours():
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    # bool sleep hours must be rejected
    with pytest.raises(ValueError, match="sleep"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=15,
            existing_slots_utc=[],
            sleep_start_hour=True,
            sleep_end_hour=8,
        )

    # float sleep hours must be rejected
    with pytest.raises(ValueError, match="sleep"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=15,
            existing_slots_utc=[],
            sleep_start_hour=1.5,
            sleep_end_hour=8.5,
        )


def test_finite_validation_bounds_daily_target():
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    # daily_target > 1000 limit
    with pytest.raises(ValueError, match="daily_target"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=1001,
            min_gap_minutes=15,
            existing_slots_utc=[],
        )

    # bool daily_target rejected
    with pytest.raises(ValueError, match="daily_target"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=True,
            min_gap_minutes=15,
            existing_slots_utc=[],
        )


def test_finite_validation_bounds_namespace():
    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)

    with pytest.raises(ValueError, match="namespace"):
        compute_audience_slot(
            now_utc=now,
            timezone_name="UTC",
            daily_target=5,
            min_gap_minutes=15,
            existing_slots_utc=[],
            namespace="x" * 257,
        )


# ── 2. Actual Runtime Burst Prevention (<10min overdue) ────────────────────────


def test_actual_runtime_burst_prevented_for_under_10min_overdue_phone_jobs(review_db):
    """When 2 Phone jobs are overdue by 8m and 3m (<10m cutoff), first on-time Phone

    must fire but second Phone must be held back to satisfy destination min_gap.
    """
    fanpage = TargetFanpage(
        name="Burst Test Project",
        target_country="ID",
        timezone="Asia/Jakarta",
        is_active=True,
        publish_daily_limit=10,
    )
    review_db.add(fanpage)
    review_db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        name="Burst Test Phone Dest",
        kind="facebook_profile",
        mode="phone",
        timezone="Asia/Jakarta",
        daily_target=6,
        phone_daily_target=6,
        repliz_daily_target=0,
        min_gap_minutes=20,  # 20 minutes min gap
        is_active=True,
        facebook_actor_id="actor_burst",
    )
    review_db.add(dest)
    review_db.flush()

    dev = PhoneDevice(
        name="device_burst",
        adb_serial="serial_burst_1",
        status="available",
        calibration_verified_at=datetime(2026, 1, 1, 0, 0, 0),
        expected_profile_actor_id="actor_burst",
    )
    review_db.add(dev)
    review_db.flush()

    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_burst",
        verified_target_actor_id="actor_burst",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    review_db.add(assign)

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    # Job 1: 8 minutes overdue (scheduled at 09:52, < 10m overdue)
    job1 = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/burst1.png",
        ai_generated_caption="Caption 1",
        status=PublishJobStatus.pending_publish,
        scheduled_for=naive_now - timedelta(minutes=8),
    )
    review_db.add(job1)
    review_db.flush()

    deliv1 = PublishDelivery(
        publish_job_id=job1.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="phone",
        scheduled_for=naive_now - timedelta(minutes=8),
        caption_snapshot="Caption 1",
        media_snapshot=["https://cdn.example.com/burst1.png"],
        media_kind="image",
        attempt_count=0,
    )
    review_db.add(deliv1)

    # Job 2: 3 minutes overdue (scheduled at 09:57, < 10m overdue)
    job2 = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/burst2.png",
        ai_generated_caption="Caption 2",
        status=PublishJobStatus.pending_publish,
        scheduled_for=naive_now - timedelta(minutes=3),
    )
    review_db.add(job2)
    review_db.flush()

    deliv2 = PublishDelivery(
        publish_job_id=job2.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="phone",
        scheduled_for=naive_now - timedelta(minutes=3),
        caption_snapshot="Caption 2",
        media_snapshot=["https://cdn.example.com/burst2.png"],
        media_kind="image",
        attempt_count=0,
    )
    review_db.add(deliv2)
    review_db.commit()

    mock_runner = MagicMock()
    mock_runner.publish_delivery.side_effect = lambda delivery, **kw: PhonePublishResult(
        status="published",
        facebook_post_id=f"post_{delivery.id}",
        permalink=f"https://fb.com/post_{delivery.id}",
        actor_id="actor_burst",
    )

    # Dispatch sweep at 10:00:00
    processed = dispatch_due_deliveries(
        review_db,
        now_utc=now,
        runner_client=mock_runner,
    )

    # Invariant: First on-time Phone job fired, BUT second Phone job held back to prevent burst!
    assert len(processed) == 1, f"Expected exactly 1 delivery dispatched, got {len(processed)}"
    assert mock_runner.publish_delivery.call_count == 1
    assert processed[0].id == deliv1.id
    assert processed[0].state == "published"

    # Second delivery must remain in scheduled state, NOT fired simultaneously
    review_db.refresh(deliv2)
    assert deliv2.state == "scheduled"
    assert deliv2.attempt_count == 0


# ── 3. Concurrency and Race Safety in Stale Replanning ────────────────────────


def test_stale_replan_ignores_concurrently_claimed_delivery(review_db):
    """If a delivery was overdue by >10m but a concurrent worker claimed it

    (state='publishing', claim_token set), replan_stale_scheduled_deliveries
    must recheck row state and NOT reschedule or overwrite it.
    """
    fanpage = TargetFanpage(
        name="Race Test Project",
        target_country="GB",
        timezone="Europe/London",
        is_active=True,
        publish_daily_limit=10,
    )
    review_db.add(fanpage)
    review_db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        name="Race Test Dest",
        kind="facebook_page",
        mode="repliz",
        timezone="Europe/London",
        daily_target=6,
        min_gap_minutes=20,
        is_active=True,
        repliz_account_id="repliz_race_1",
    )
    review_db.add(dest)
    review_db.flush()

    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    # Delivery overdue by 15m (>10m cutoff)
    old_sched = naive_now - timedelta(minutes=15)
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/race.png",
        ai_generated_caption="Race caption",
        status=PublishJobStatus.pending_publish,
        scheduled_for=old_sched,
    )
    review_db.add(job)
    review_db.flush()

    deliv = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="publishing",  # Concurrently claimed!
        claim_token="concurrent_token_123",
        intended_method="repliz",
        scheduled_for=old_sched,
        caption_snapshot="Race caption",
        media_snapshot=["https://cdn.example.com/race.png"],
        media_kind="image",
        attempt_count=0,
    )
    review_db.add(deliv)
    review_db.commit()

    replanned = replan_stale_scheduled_deliveries(review_db, now_utc=now)
    assert len(replanned) == 0

    review_db.refresh(deliv)
    assert deliv.state == "publishing"
    assert deliv.claim_token == "concurrent_token_123"
    assert deliv.scheduled_for == old_sched


def test_stale_replan_syncs_parent_publish_job_scheduled_for(review_db):
    """When a stale delivery is rescheduled to a future slot, the parent

    PublishJob.scheduled_for must also be synchronized if matching old slot.
    """
    fanpage = TargetFanpage(
        name="Sync Test Project",
        target_country="GB",
        timezone="Europe/London",
        is_active=True,
        publish_daily_limit=10,
    )
    review_db.add(fanpage)
    review_db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        name="Sync Test Dest",
        kind="facebook_page",
        mode="repliz",
        timezone="Europe/London",
        daily_target=6,
        min_gap_minutes=20,
        is_active=True,
        repliz_account_id="repliz_sync_1",
    )
    review_db.add(dest)
    review_db.flush()

    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)
    old_sched = naive_now - timedelta(minutes=25)

    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/sync.png",
        ai_generated_caption="Sync caption",
        status=PublishJobStatus.pending_publish,
        scheduled_for=old_sched,
    )
    review_db.add(job)
    review_db.flush()

    deliv = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=old_sched,
        caption_snapshot="Sync caption",
        media_snapshot=["https://cdn.example.com/sync.png"],
        media_kind="image",
        attempt_count=0,
    )
    review_db.add(deliv)
    review_db.commit()

    replanned = replan_stale_scheduled_deliveries(review_db, now_utc=now)
    assert len(replanned) == 1

    review_db.refresh(deliv)
    review_db.refresh(job)

    assert deliv.scheduled_for > naive_now
    assert job.scheduled_for == deliv.scheduled_for, "Parent job.scheduled_for was not synced!"


# ── 4. Remote Immutability ───────────────────────────────────────────────────


def test_remote_immutability_never_moves_published_or_uncertain(review_db):
    """Rows with repliz_schedule_id, facebook_post_id, or uncertain_since

    must NEVER be moved by stale replan.
    """
    fanpage = TargetFanpage(
        name="Immutability Project",
        target_country="GB",
        timezone="Europe/London",
        is_active=True,
        publish_daily_limit=10,
    )
    review_db.add(fanpage)
    review_db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        name="Immutability Dest",
        kind="facebook_page",
        mode="repliz",
        timezone="Europe/London",
        daily_target=6,
        min_gap_minutes=20,
        is_active=True,
        repliz_account_id="repliz_imm_1",
    )
    review_db.add(dest)
    review_db.flush()

    now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)
    stale_time = naive_now - timedelta(minutes=30)

    # 1. Row with repliz_schedule_id
    deliv_repliz = PublishDelivery(
        publish_job_id=1,
        destination_id=dest.id,
        state="repliz_scheduled",
        repliz_schedule_id="ext_repliz_999",
        scheduled_for=stale_time,
        caption_snapshot="Snap",
        media_snapshot=["https://cdn.example.com/1.png"],
        media_kind="image",
    )
    # 2. Row with facebook_post_id
    deliv_fb = PublishDelivery(
        publish_job_id=2,
        destination_id=dest.id,
        state="published",
        facebook_post_id="ext_fb_888",
        scheduled_for=stale_time,
        caption_snapshot="Snap",
        media_snapshot=["https://cdn.example.com/2.png"],
        media_kind="image",
    )
    # 3. Row with uncertain_since
    deliv_unc = PublishDelivery(
        publish_job_id=3,
        destination_id=dest.id,
        state="uncertain",
        uncertain_since=stale_time,
        scheduled_for=stale_time,
        caption_snapshot="Snap",
        media_snapshot=["https://cdn.example.com/3.png"],
        media_kind="image",
    )
    review_db.add_all([deliv_repliz, deliv_fb, deliv_unc])
    review_db.commit()

    replanned = replan_stale_scheduled_deliveries(review_db, now_utc=now)
    assert len(replanned) == 0

    review_db.refresh(deliv_repliz)
    review_db.refresh(deliv_fb)
    review_db.refresh(deliv_unc)
    assert deliv_repliz.scheduled_for == stale_time
    assert deliv_fb.scheduled_for == stale_time
    assert deliv_unc.scheduled_for == stale_time


# ── 5. DST and Sleep Boundary Semantics ──────────────────────────────────────


def test_dst_and_sleep_boundaries():
    # 1. Equal start and end hour means 24h active (no sleep window)
    now = datetime(2026, 10, 15, 2, 0, 0, tzinfo=timezone.utc)
    slot_24h = compute_audience_slot(
        now_utc=now,
        timezone_name="UTC",
        daily_target=24,
        min_gap_minutes=5,
        existing_slots_utc=[],
        sleep_start_hour=5,
        sleep_end_hour=5,  # Equal -> no sleep
    )
    # Since 24h is active, a candidate early in the morning can be allocated
    assert slot_24h >= now + timedelta(seconds=60)

    # 2. Daytime sleep 7->22 (active only overnight 22:00->07:00)
    slot_day_sleep = compute_audience_slot(
        now_utc=datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc),
        timezone_name="UTC",
        daily_target=4,
        min_gap_minutes=30,
        existing_slots_utc=[],
        sleep_start_hour=7,
        sleep_end_hour=22,
    )
    assert slot_day_sleep.hour >= 22 or slot_day_sleep.hour < 7

    # 3. London autumn DST backward transition (late October)
    # Sunday 2026-10-25: 02:00 BST -> 01:00 GMT (repeated hour)
    london_dst_fall = datetime(2026, 10, 24, 20, 0, 0, tzinfo=timezone.utc)
    slot_dst = compute_audience_slot(
        now_utc=london_dst_fall,
        timezone_name="Europe/London",
        daily_target=10,
        min_gap_minutes=15,
        existing_slots_utc=[],
    )
    # Must be valid aware UTC datetime roundtripping cleanly
    assert slot_dst.tzinfo is not None
    local_dst = slot_dst.astimezone(zoneinfo.ZoneInfo("Europe/London"))
    assert 7 <= local_dst.hour < 22
