"""Integration tests for audience-aware delivery scheduling, legacy reservations, and recovery."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock
import zoneinfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
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
from app.services.social_delivery_dispatch import (
    dispatch_due_deliveries,
    replan_recovering_phone_deliveries,
)
from app.services.social_delivery_planner import ensure_deliveries


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite fixture with proper compiler cleanup."""
    orig_visit_array = getattr(SQLiteTypeCompiler, "visit_ARRAY", None)
    orig_visit_jsonb = getattr(SQLiteTypeCompiler, "visit_JSONB", None)
    orig_visit_uuid = getattr(SQLiteTypeCompiler, "visit_UUID", None)
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
        if orig_visit_array is not None:
            SQLiteTypeCompiler.visit_ARRAY = orig_visit_array
        elif hasattr(SQLiteTypeCompiler, "visit_ARRAY"):
            delattr(SQLiteTypeCompiler, "visit_ARRAY")
        if orig_visit_jsonb is not None:
            SQLiteTypeCompiler.visit_JSONB = orig_visit_jsonb
        elif hasattr(SQLiteTypeCompiler, "visit_JSONB"):
            delattr(SQLiteTypeCompiler, "visit_JSONB")
        if orig_visit_uuid is not None:
            SQLiteTypeCompiler.visit_UUID = orig_visit_uuid
        elif hasattr(SQLiteTypeCompiler, "visit_UUID"):
            delattr(SQLiteTypeCompiler, "visit_UUID")


def _create_project_and_dest(
    db,
    name="Formula Weekend",
    timezone_name="Australia/Sydney",
    daily_target=31,
    min_gap_minutes=5,
    sleep_start=22,
    sleep_end=8,
    mode="phone",
    kind=None,
    repliz_account_id=None,
):
    fanpage = TargetFanpage(
        name=name,
        repliz_account_id=f"rep_{name}",
        timezone=timezone_name,
        publish_sleep_start_hour=sleep_start,
        publish_sleep_end_hour=sleep_end,
        is_active=True,
    )
    db.add(fanpage)
    db.flush()

    if kind is None:
        kind = "facebook_profile" if mode == "phone" else "facebook_page"

    if repliz_account_id is None and mode in ("repliz", "mixed"):
        repliz_account_id = fanpage.repliz_account_id

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind=kind,
        name=f"{name} Dest",
        facebook_actor_id=f"actor_{name}",
        parent_profile_id="prof_parent" if kind == "facebook_page" and mode in ("phone", "mixed") else None,
        repliz_account_id=repliz_account_id,
        mode=mode,
        timezone=timezone_name,
        daily_target=daily_target,
        phone_daily_target=daily_target if mode == "phone" else 0,
        repliz_daily_target=0 if mode == "phone" else daily_target,
        min_gap_minutes=min_gap_minutes,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    db.add(dest)
    db.flush()
    return fanpage, dest


def test_ensure_deliveries_formula_sydney_six_posts_no_five_minute_burst(db_session):
    """Regression: 6 queued posts on Sydney target 31 gap 5 must not all be 5m apart."""
    fanpage, dest = _create_project_and_dest(
        db_session,
        timezone_name="Australia/Sydney",
        daily_target=31,
        min_gap_minutes=5,
        sleep_start=22,
        sleep_end=8,
    )

    now_utc = datetime(2026, 10, 20, 0, 48, 35, tzinfo=timezone.utc)
    deliveries = []

    for i in range(6):
        job = PublishJob(
            fanpage_id=fanpage.id,
            content_type=ContentType.news_content,
            design_title=f"Formula Video {i}",
            design_image_url=f"https://cdn.example.com/img{i}.jpg",
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(job)
        db_session.flush()

        delivs = ensure_deliveries(db_session, job, now_utc=now_utc)
        assert len(delivs) == 1
        deliveries.append(delivs[0])

    # Check scheduled times
    scheduled_times = [d.scheduled_for for d in deliveries]
    gaps = [
        (scheduled_times[i + 1] - scheduled_times[i]).total_seconds()
        for i in range(5)
    ]

    # Must NOT all be 300 seconds (5 minutes)
    assert any(g != 300.0 for g in gaps), f"Gaps were all 5 minutes: {gaps}"
    # Every gap must be at least 300s (5m)
    for g in gaps:
        assert g >= 300.0


def test_replan_recovering_phone_deliveries_respects_sleep_window(db_session):
    """Regression: phone recovery must respect project sleep window."""
    # Sleep 22 to 8 Sydney time
    fanpage, dest = _create_project_and_dest(
        db_session,
        timezone_name="Australia/Sydney",
        daily_target=10,
        min_gap_minutes=15,
        sleep_start=22,
        sleep_end=8,
    )
    sydney_tz = zoneinfo.ZoneInfo("Australia/Sydney")

    # Add available device
    device = PhoneDevice(
        name="Pixel 7",
        adb_serial="serial_001",
        status="available",
        calibration_verified_at=datetime(2026, 10, 19, 0, 0),
    )
    db_session.add(device)
    db_session.flush()
    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        priority=1,
        is_active=True,
        verified_at=datetime(2026, 10, 19, 0, 0),
        verified_profile_actor_id=dest.facebook_actor_id,
        verified_target_actor_id=dest.facebook_actor_id,
    )
    db_session.add(assignment)
    db_session.flush()

    # Create waiting delivery
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/img.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    # Now is 23:00 Sydney time (inside sleep window 22:00 -> 08:00)
    # 23:00 AEDT on 2026-10-20 = 12:00 UTC on 2026-10-20
    now_utc = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        intended_method="phone",
        state="waiting_for_device",
        media_kind="image",
        content_type_snapshot="news_content",
    )
    db_session.add(delivery)
    db_session.commit()

    replanned = replan_recovering_phone_deliveries(
        db_session, device_id=device.id, now_utc=now_utc
    )
    assert len(replanned) == 1
    new_slot = replanned[0].scheduled_for
    assert new_slot is not None

    new_slot_aware = new_slot.replace(tzinfo=timezone.utc).astimezone(sydney_tz)
    # Must NOT be during sleep window (22:00 to 08:00)
    assert 8 <= new_slot_aware.hour < 22


def test_legacy_jobs_counted_in_reservations_and_daily_cap(db_session):
    """Legacy PublishJob records without ledger rows are counted towards matching Repliz destinations."""
    fanpage, dest = _create_project_and_dest(
        db_session,
        timezone_name="UTC",
        daily_target=2,
        min_gap_minutes=30,
        sleep_start=22,
        sleep_end=8,
        mode="repliz",
    )

    # Legacy job with scheduled_for today at 10:00 UTC, no ledger rows
    legacy_job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/legacy.jpg",
        status=PublishJobStatus.pending_publish,
        scheduled_for=datetime(2026, 10, 20, 10, 0, 0),
        is_deleted=False,
    )
    db_session.add(legacy_job)
    db_session.flush()

    # Now schedule 2 new jobs with daily_target = 2
    now_utc = datetime(2026, 10, 20, 9, 0, 0, tzinfo=timezone.utc)

    job1 = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/job1.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job1)
    db_session.flush()
    deliv1 = ensure_deliveries(db_session, job1, now_utc=now_utc)[0]

    job2 = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/job2.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job2)
    db_session.flush()
    deliv2 = ensure_deliveries(db_session, job2, now_utc=now_utc)[0]

    # Legacy job was slot 1 today. deliv1 was slot 2 today.
    # deliv2 should exceed daily_target=2 and roll over to tomorrow!
    assert deliv1.scheduled_for.date() == datetime(2026, 10, 20).date()
    assert deliv2.scheduled_for.date() > datetime(2026, 10, 20).date()


def test_legacy_jobs_review_flags_coverage(db_session):
    """Verify review flags: future published job reservation, repliz matching isolation, soft-deleted, sibling ledger."""
    from app.services.publishing_schedule import get_destination_reservations_utc

    fanpage, dest_repliz = _create_project_and_dest(
        db_session,
        name="Project Flags",
        timezone_name="UTC",
        daily_target=5,
        min_gap_minutes=15,
        mode="repliz",
    )

    # 1. Sibling phone destination on same project
    dest_phone = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_profile",
        name="Sibling Phone",
        facebook_actor_id="actor_sibling_phone",
        mode="phone",
        timezone="UTC",
        daily_target=5,
        phone_daily_target=5,
        repliz_daily_target=0,
        min_gap_minutes=15,
        supported_media=["image"],
        is_active=True,
    )
    db_session.add(dest_phone)
    db_session.flush()

    # 2. Legacy job published NOW but scheduled for FUTURE (Repliz accepted now for go-live tomorrow)
    future_slot = datetime(2026, 10, 21, 15, 0, 0)
    now_slot = datetime(2026, 10, 20, 10, 0, 0)
    job_future_pub = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.published,
        published_at=now_slot,
        scheduled_for=future_slot,
        is_deleted=False,
    )
    # 3. Soft-deleted legacy job
    job_deleted = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_publish,
        scheduled_for=datetime(2026, 10, 20, 12, 0, 0),
        is_deleted=True,
    )
    # 4. Job that has ledger delivery on sibling phone destination
    job_with_ledger = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_publish,
        scheduled_for=datetime(2026, 10, 20, 13, 0, 0),
        is_deleted=False,
    )
    db_session.add_all([job_future_pub, job_deleted, job_with_ledger])
    db_session.flush()

    sibling_deliv = PublishDelivery(
        publish_job_id=job_with_ledger.id,
        destination_id=dest_phone.id,
        intended_method="phone",
        state="scheduled",
        scheduled_for=datetime(2026, 10, 20, 13, 0, 0),
    )
    db_session.add(sibling_deliv)
    db_session.commit()

    # Repliz destination reservations
    repliz_res = get_destination_reservations_utc(db_session, dest_repliz)
    # Future scheduled_for preserved (not squashed to published_at)
    assert future_slot.replace(tzinfo=timezone.utc) in repliz_res
    # Soft deleted job excluded
    assert datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc) not in repliz_res
    # Job with sibling ledger excluded from legacy query (no double-count)
    assert datetime(2026, 10, 20, 13, 0, 0, tzinfo=timezone.utc) not in repliz_res

    # Phone destination reservations
    phone_res = get_destination_reservations_utc(db_session, dest_phone)
    # Phone destination must NOT inherit historical Repliz jobs
    assert future_slot.replace(tzinfo=timezone.utc) not in phone_res
    # Phone destination only sees its own delivery
    assert datetime(2026, 10, 20, 13, 0, 0, tzinfo=timezone.utc) in phone_res


def test_overdue_untouched_scheduled_replan_before_dispatch(db_session):
    """Untouched delivery > 10 min late is replanned to future candidate, not immediately flushed."""
    fanpage, dest = _create_project_and_dest(
        db_session,
        timezone_name="UTC",
        daily_target=10,
        min_gap_minutes=15,
        sleep_start=22,
        sleep_end=8,
    )
    now_utc = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    stale_time = datetime(2026, 10, 20, 13, 0, 0)  # 60 min late (> 10 min)

    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/img.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        intended_method="phone",
        state="scheduled",
        scheduled_for=stale_time,
        attempt_count=0,
        repliz_schedule_id=None,
        media_kind="image",
        content_type_snapshot="news_content",
    )
    db_session.add(delivery)
    db_session.commit()

    fake_runner = Mock()
    fake_runner.run_repost.return_value = Mock(status="completed", success=True)

    # Dispatch sweep should replan the stale delivery into the future instead of immediate dispatch
    processed = dispatch_due_deliveries(
        db_session,
        job_id=job.id,
        now_utc=now_utc,
        runner_client=fake_runner,
    )

    db_session.refresh(delivery)
    # Runner must NOT be called for stale replanned delivery
    assert fake_runner.run_repost.call_count == 0
    # Delivery must be moved to the future >= now + 60s
    assert delivery.scheduled_for >= datetime(2026, 10, 20, 14, 1, 0)


def test_ontime_due_phone_delivery_dispatches_while_future_does_not(db_session):
    """On-time due Phone delivery dispatches through runner; future candidate makes zero runner calls."""
    fanpage, dest = _create_project_and_dest(
        db_session,
        timezone_name="UTC",
        daily_target=10,
        min_gap_minutes=15,
        sleep_start=22,
        sleep_end=8,
    )

    naive_now = datetime(2026, 10, 20, 14, 0, 0)
    now_utc = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)

    device = PhoneDevice(
        name="Pixel 8",
        adb_serial="serial_002",
        status="available",
        calibration_verified_at=naive_now - timedelta(days=1),
    )
    db_session.add(device)
    db_session.flush()
    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        priority=1,
        is_active=True,
        verified_at=naive_now - timedelta(days=1),
        verified_profile_actor_id=dest.facebook_actor_id,
        verified_target_actor_id=dest.facebook_actor_id,
    )
    db_session.add(assignment)
    db_session.flush()

    # Delivery 1: on-time due (scheduled for 2 minutes ago: within 10 min window)
    job1 = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/due.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job1)
    db_session.flush()
    deliv_due = PublishDelivery(
        publish_job_id=job1.id,
        destination_id=dest.id,
        device_id=device.id,
        intended_method="phone",
        state="scheduled",
        scheduled_for=naive_now - timedelta(minutes=2),
        attempt_count=0,
        media_kind="image",
        content_type_snapshot="news_content",
    )
    db_session.add(deliv_due)

    # Delivery 2: in the future (scheduled for 30 minutes from now)
    job2 = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/future.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job2)
    db_session.flush()
    deliv_future = PublishDelivery(
        publish_job_id=job2.id,
        destination_id=dest.id,
        device_id=device.id,
        intended_method="phone",
        state="scheduled",
        scheduled_for=naive_now + timedelta(minutes=30),
        attempt_count=0,
        media_kind="image",
        content_type_snapshot="news_content",
    )
    db_session.add(deliv_future)
    db_session.commit()

    fake_runner = Mock()
    fake_runner.publish_delivery.return_value = Mock(
        status="published",
        facebook_post_id="post_123",
        actor_id=dest.facebook_actor_id,
        permalink="https://facebook.com/post_123",
        error_code=None,
        publish_may_have_started=False,
    )

    dispatch_due_deliveries(
        db_session,
        now_utc=now_utc,
        runner_client=fake_runner,
    )

    db_session.refresh(deliv_due)
    db_session.refresh(deliv_future)

    # On-time due delivery was dispatched through runner
    assert fake_runner.publish_delivery.call_count == 1
    # Future delivery remains scheduled, zero runner calls
    assert deliv_future.state == "scheduled"
    assert deliv_future.scheduled_for == naive_now + timedelta(minutes=30)


def test_repeated_recovery_does_not_slide_future_slot_forever(db_session):
    """When recovery runs multiple times, an untouched scheduled future delivery is not slid forward forever."""
    fanpage, dest = _create_project_and_dest(
        db_session,
        timezone_name="UTC",
        daily_target=10,
        min_gap_minutes=15,
        sleep_start=22,
        sleep_end=8,
    )
    now_utc = datetime(2026, 10, 20, 14, 0, 0, tzinfo=timezone.utc)
    future_slot = datetime(2026, 10, 20, 15, 30, 0)

    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/img.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        intended_method="phone",
        state="scheduled",
        scheduled_for=future_slot,
        attempt_count=0,
        media_kind="image",
        content_type_snapshot="news_content",
    )
    db_session.add(delivery)
    db_session.commit()

    # Call dispatch recovery 3 times over 5 minutes
    for minute_offset in (0, 2, 5):
        dispatch_due_deliveries(
            db_session,
            now_utc=now_utc + timedelta(minutes=minute_offset),
        )
        db_session.refresh(delivery)
        # Scheduled time must remain steady, not sliding further into the future
        assert delivery.scheduled_for == future_slot


def test_postgresql_query_compilation_safety():
    """Verify shared reservation queries compile cleanly under PostgreSQL dialect."""
    from app.services.publishing_schedule import build_reservations_query

    query = build_reservations_query(destination_id=1, content_project_id=10)
    compiled = str(query.compile(dialect=postgresql.dialect()))
    # Must not contain DISTINCT on JSON columns
    assert "DISTINCT" not in compiled.upper() or "JSON" not in compiled.upper()
    # Must not contain FOR UPDATE on outer joins
    assert "FOR UPDATE" not in compiled.upper()
