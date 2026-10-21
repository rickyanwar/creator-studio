"""Focused tests for social delivery dispatch (S1b).

Verifies:
1. Exact-once ledger claim & idempotency
2. Uncertain-on-timeout / 5xx (no automatic resubmission)
3. Safe pre-Publish wait (phone waits for due slot)
4. Lease exclusivity and stale lease/claim recovery
5. Repliz Page vs Phone Profile separation
6. Mixed 1+5 schedule (Repliz ahead-of-time, phone paced)
7. Spaced recovery (no backlog burst on phone revival)
8. Unsupported media stays waiting visibly
9. publish_job integration & legacy fallback
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

# Allow SQLite to adapt list/dict objects and compile PostgreSQL ARRAY and JSONB types
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

import httpx

from app.models.posts import MediaType, Post, PostStatus
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
from app.services.phone_runner_client import PhoneRecoveryResult, PhoneRunnerClient
from app.services.social_delivery_dispatch import (
    PhonePublishResult,
    _compose_phone_last_error,
    claim_delivery_for_dispatch,
    dispatch_due_deliveries,
    dispatch_phone_delivery,
    dispatch_repliz_delivery,
    evaluate_and_execute_fallback,
    execute_device_recovery,
    notify_owner_via_hermes,
    reconcile_and_verify_absence,
    recover_stale_deliveries_and_leases,
    release_device_lease,
    replan_recovering_phone_deliveries,
    select_and_lease_phone_device,
)
from app.services.social_delivery_planner import ensure_deliveries
from app.tasks.publisher import publish_job


@pytest.fixture
def db_session():
    """Create isolated in-memory SQLite database and session."""
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
    DeviceLease.__table__.create(engine)
    RecoveryIncident.__table__.create(engine)
    OperationalNotification.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        sd_col.server_default = orig_sd
        de_col.server_default = orig_de


def _create_project_and_dest(
    db,
    name="Test Project",
    kind="facebook_page",
    mode="repliz",
    repliz_id="acc_123",
    actor_id="actor_page_1",
    parent_profile_id=None,
    daily_target=1,
    phone_target=0,
    repliz_target=1,
    tz="UTC",
):
    fanpage = TargetFanpage(
        name=name,
        repliz_account_id=repliz_id,
        is_active=True,
    )
    db.add(fanpage)
    db.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind=kind,
        name=f"{name} Destination",
        facebook_actor_id=actor_id,
        parent_profile_id=parent_profile_id,
        repliz_account_id=repliz_id,
        mode=mode,
        timezone=tz,
        daily_target=daily_target,
        phone_daily_target=phone_target,
        repliz_daily_target=repliz_target,
        min_gap_minutes=15,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    db.add(dest)
    db.flush()
    db.commit()
    return fanpage, dest


def _create_phone_device(
    db,
    name="Phone 1",
    actor_id="actor_prof_1",
    status="available",
    calibrated=True,
    adb_serial=None,
):
    dev = PhoneDevice(
        name=name,
        expected_profile_actor_id=actor_id,
        status=status,
        tailscale_ip="100.64.0.1",
        adb_serial=adb_serial or f"serial_{name.lower().replace(' ', '_')}",
        calibration_verified_at=datetime(2026, 1, 1, 0, 0, 0) if calibrated else None,
    )
    db.add(dev)
    db.flush()
    db.commit()
    return dev


def _create_job_and_delivery(
    db,
    fanpage,
    dest,
    intended_method="repliz",
    state="scheduled",
    scheduled_for=None,
    media_kind="image",
):
    now = datetime(2026, 10, 15, 10, 0, 0)
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/img.png",
        ai_generated_caption="Headline news",
        status=PublishJobStatus.pending_publish,
    )
    db.add(job)
    db.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state=state,
        intended_method=intended_method,
        scheduled_for=scheduled_for or now,
        caption_snapshot="Headline news",
        media_snapshot=["https://cdn.example.com/img.png"],
        media_kind=media_kind,
    )
    db.add(delivery)
    db.flush()
    db.commit()
    return job, delivery


# ── Test 1: Exact-once ledger claim & idempotency ─────────────────────────────


def test_exact_once_ledger_claim(db_session):
    fanpage, dest = _create_project_and_dest(db_session)
    job, delivery = _create_job_and_delivery(db_session, fanpage, dest)

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)

    # First claim succeeds
    claimed_deliv, token1 = claim_delivery_for_dispatch(
        db_session, delivery.id, eligible_states=["scheduled"], now_utc=now
    )
    assert claimed_deliv is not None
    assert claimed_deliv.state == "publishing"
    assert claimed_deliv.claim_token == token1
    assert token1 is not None

    # Second concurrent claim finds state != scheduled and fails
    claimed_second, token2 = claim_delivery_for_dispatch(
        db_session, delivery.id, eligible_states=["scheduled"], now_utc=now
    )
    assert claimed_second is None
    assert token2 is None

    # DeliveryEvent recorded
    events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
    assert len(events) == 1
    assert events[0].event_type == "claim_acquired"


# ── Test 2: Uncertain on timeout / 5xx (never auto-resubmitted) ────────────────


def test_uncertain_on_repliz_timeout(db_session):
    fanpage, dest = _create_project_and_dest(db_session)
    job, delivery = _create_job_and_delivery(db_session, fanpage, dest)

    mock_client = MagicMock()
    req = httpx.Request("POST", "https://api.repliz.com/public/schedule")
    mock_client.create_image_schedule.side_effect = httpx.ReadTimeout(
        "Connection timed out", request=req
    )

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    res = dispatch_repliz_delivery(db_session, delivery, repliz_client=mock_client, now_utc=now)

    assert res.state == "uncertain"
    assert res.uncertain_since is not None
    assert "timeout" in res.last_error.lower()
    assert res.attempt_count == 1

    # Next dispatch tick does NOT pick up uncertain delivery
    processed = dispatch_due_deliveries(db_session, now_utc=now, repliz_client=mock_client)
    assert len(processed) == 0


def test_uncertain_on_repliz_500_server_error(db_session):
    fanpage, dest = _create_project_and_dest(db_session)
    job, delivery = _create_job_and_delivery(db_session, fanpage, dest)

    mock_client = MagicMock()
    req = httpx.Request("POST", "https://api.repliz.com/public/schedule")
    resp = httpx.Response(502, request=req, text="Bad Gateway")
    mock_client.create_image_schedule.side_effect = httpx.HTTPStatusError(
        "502 Server Error", request=req, response=resp
    )

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    res = dispatch_repliz_delivery(db_session, delivery, repliz_client=mock_client, now_utc=now)

    assert res.state == "uncertain"
    assert "5xx" in res.last_error


def test_uncertain_on_phone_runner_timeout(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session, kind="facebook_profile", mode="phone", repliz_id=None, actor_id="actor_p1", daily_target=1, phone_target=1, repliz_target=0
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone"
    )

    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="uncertain",
        publish_may_have_started=True,
        error_code="portal_timeout_after_publish_tap",
    )

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "uncertain"
    assert res.uncertain_since is not None
    # Device lease must be released
    assert db_session.query(DeviceLease).filter_by(device_id=dev.id).count() == 0


# ── Test 3: Safe pre-Publish wait (Phone delivery waits for due slot) ──────────


def test_safe_pre_publish_wait(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session, kind="facebook_profile", mode="phone", repliz_id=None, actor_id="actor_p1", daily_target=1, phone_target=1, repliz_target=0
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    base_time = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    future_slot = base_time + timedelta(hours=2)

    job, delivery = _create_job_and_delivery(
        db_session,
        fanpage,
        dest,
        intended_method="phone",
        state="scheduled",
        scheduled_for=future_slot.replace(tzinfo=None),
    )

    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post_999",
        permalink="https://fb.com/post_999",
        actor_id="actor_p1",
    )

    # 1. Dispatch at base_time: delivery is in the future -> NOT dispatched
    processed = dispatch_due_deliveries(
        db_session, now_utc=base_time, runner_client=mock_runner
    )
    assert len(processed) == 0
    assert mock_runner.publish_delivery.call_count == 0

    deliv_check = db_session.query(PublishDelivery).filter_by(id=delivery.id).first()
    assert deliv_check.state == "scheduled"

    # 2. Advance time past scheduled_for -> dispatched successfully
    due_time = future_slot + timedelta(minutes=1)
    processed = dispatch_due_deliveries(
        db_session, now_utc=due_time, runner_client=mock_runner
    )
    assert len(processed) == 1
    assert mock_runner.publish_delivery.call_count == 1
    assert processed[0].state == "published"
    assert processed[0].facebook_post_id == "post_999"


# ── Test 4: Lease exclusivity and stale recovery ──────────────────────────────


def test_lease_exclusivity_and_stale_recovery(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session, kind="facebook_profile", mode="phone", repliz_id=None, actor_id="actor_p1", daily_target=2, phone_target=2, repliz_target=0
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job1, deliv1 = _create_job_and_delivery(db_session, fanpage, dest, intended_method="phone")
    job2, deliv2 = _create_job_and_delivery(db_session, fanpage, dest, intended_method="phone")

    # Delivery 1 acquires lease
    leased_dev, err = select_and_lease_phone_device(db_session, deliv1, now_utc=now, lease_ttl_minutes=45)
    assert leased_dev is not None
    assert leased_dev.id == dev.id

    # Delivery 2 tries to lease while lease is active -> blocked!
    leased_dev2, err2 = select_and_lease_phone_device(db_session, deliv2, now_utc=now, lease_ttl_minutes=45)
    assert leased_dev2 is None
    assert "no eligible and available" in err2.lower()

    # Worker death simulation: deliv1 was in 'publishing' state, time advances 50 minutes past TTL
    deliv1.state = "publishing"
    deliv1.claimed_at = (now - timedelta(minutes=50)).replace(tzinfo=None)
    db_session.commit()

    later = now + timedelta(minutes=50)
    recovered = recover_stale_deliveries_and_leases(db_session, now_utc=later, stale_ttl_minutes=45)
    assert recovered >= 1

    d1 = db_session.query(PublishDelivery).filter_by(id=deliv1.id).first()
    assert d1.state == "uncertain"

    # Delivery 2 can now acquire the leased phone
    leased_dev2_now, err_none = select_and_lease_phone_device(db_session, deliv2, now_utc=later, lease_ttl_minutes=45)
    assert leased_dev2_now is not None
    assert leased_dev2_now.id == dev.id


# ── Test 5: Repliz Page vs Phone Profile separation ───────────────────────────


def test_repliz_page_phone_profile_separation(db_session):
    # 1. Profile cannot publish via Repliz
    fanpage_p, dest_p = _create_project_and_dest(
        db_session, kind="facebook_profile", mode="phone", repliz_id=None, actor_id="actor_p", daily_target=1, phone_target=1, repliz_target=0
    )
    job_p, deliv_p = _create_job_and_delivery(db_session, fanpage_p, dest_p, intended_method="repliz")

    res = dispatch_repliz_delivery(db_session, deliv_p)
    assert res.state == "failed"
    assert "profiles cannot publish via repliz" in res.last_error.lower()

    # 2. Page via Repliz succeeds
    fanpage_page, dest_page = _create_project_and_dest(
        db_session, kind="facebook_page", mode="repliz", repliz_id="repliz_page_99"
    )
    job_page, deliv_page = _create_job_and_delivery(db_session, fanpage_page, dest_page, intended_method="repliz")

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.return_value = {"id": "sched_12345"}

    res_page = dispatch_repliz_delivery(db_session, deliv_page, repliz_client=mock_repliz)
    assert res_page.state == "repliz_scheduled"
    assert res_page.repliz_schedule_id == "sched_12345"


# ── Test 6: Mixed 1+5 schedule ────────────────────────────────────────────────


def test_mixed_1_plus_5_schedule(db_session):
    # Mixed destination: 1 phone + 5 repliz
    # Page requires parent profile for phone
    parent_fanpage, parent_prof = _create_project_and_dest(
        db_session, kind="facebook_profile", mode="phone", repliz_id=None, actor_id="main_actor", daily_target=1, phone_target=1, repliz_target=0
    )

    page_fanpage, mixed_dest = _create_project_and_dest(
        db_session,
        name="Mixed Fanpage",
        kind="facebook_page",
        mode="mixed",
        repliz_id="mixed_acc_1",
        actor_id="page_actor_1",
        parent_profile_id=parent_prof.id,
        daily_target=6,
        phone_target=1,
        repliz_target=5,
    )

    dev = _create_phone_device(db_session, actor_id="main_actor")
    assign = DestinationDeviceAssignment(
        destination_id=mixed_dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="main_actor",
        verified_target_actor_id="page_actor_1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 7, 0, 0, tzinfo=timezone.utc)

    # Create 6 jobs for the mixed destination
    deliveries = []
    for i in range(6):
        job = PublishJob(
            fanpage_id=page_fanpage.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://cdn.example.com/img_{i}.png",
            ai_generated_caption=f"News caption {i}",
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(job)
        db_session.flush()
        d_list = ensure_deliveries(db_session, job, now_utc=now)
        deliveries.extend(d_list)

    assert len(deliveries) == 6
    # Slot 0 is phone; slots 1-5 are repliz
    assert deliveries[0].intended_method == "phone"
    assert all(d.intended_method == "repliz" for d in deliveries[1:])

    # Mock repliz and phone runners
    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.side_effect = lambda **kwargs: {"id": f"sched_{kwargs['description']}"}

    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post_phone_1",
        permalink="https://fb.com/post_phone_1",
        actor_id="page_actor_1",
    )

    # Dispatch: Repliz deliveries (1-5) can be submitted ahead of scheduleAt!
    # Phone delivery (0) is scheduled in the future (>= now + 60s) so it waits for its slot.
    processed_first = dispatch_due_deliveries(
        db_session,
        now_utc=now,
        repliz_client=mock_repliz,
        runner_client=mock_runner,
    )

    # 5 Repliz deliveries submitted ahead of time; Phone waits for due slot
    assert len(processed_first) == 5
    assert mock_repliz.create_image_schedule.call_count == 5
    assert mock_runner.publish_delivery.call_count == 0

    # Advance clock to Phone delivery's actual scheduled slot
    phone_slot = deliveries[0].scheduled_for.replace(tzinfo=timezone.utc)
    processed_second = dispatch_due_deliveries(
        db_session,
        now_utc=phone_slot,
        repliz_client=mock_repliz,
        runner_client=mock_runner,
    )
    assert len(processed_second) == 1
    assert mock_runner.publish_delivery.call_count == 1
    assert len(processed_first) + len(processed_second) == 6


# ── Test 7: Spaced recovery (no backlog burst on phone revival) ────────────────


def test_spaced_recovery_no_backlog_burst(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
        tz="UTC",
    )
    dev = _create_phone_device(db_session, actor_id="actor_p", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id, device_id=dev.id, priority=0, is_active=True
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)

    # 3 deliveries created while phone is in maintenance -> waiting_for_device
    delivs = []
    for i in range(3):
        job = PublishJob(
            fanpage_id=fanpage.id,
            content_type=ContentType.discussion,
            design_image_url=f"https://cdn.example.com/img_{i}.png",
            ai_generated_caption=f"News caption {i}",
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(job)
        db_session.flush()
        d_list = ensure_deliveries(db_session, job, now_utc=now)
        delivs.extend(d_list)

    assert len(delivs) == 3
    assert all(d.state == "waiting_for_device" for d in delivs)

    # Phone recovers!
    dev.status = "available"
    db_session.commit()

    # Replan waiting deliveries
    replanned = replan_recovering_phone_deliveries(db_session, device_id=dev.id, now_utc=now)
    assert len(replanned) == 3
    assert all(d.state == "scheduled" for d in replanned)

    # With daily_target = 1, the 3 posts MUST be spaced across 3 distinct days:
    dates = [d.scheduled_for.date() for d in replanned]
    assert len(set(dates)) == 3
    assert dates[0] < dates[1] < dates[2]


# ── Test 8: Unsupported media stays waiting ───────────────────────────────────


def test_unsupported_media_stays_waiting(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    # Only support images
    dest.supported_media = ["image"]
    dev = _create_phone_device(db_session, actor_id="actor_p", status="available")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p",
        verified_target_actor_id="actor_p",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    # Create video job
    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.youtube_clip,
        video_url="https://cdn.example.com/video.mp4",
        ai_generated_caption="Clip",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    d_list = ensure_deliveries(db_session, job, now_utc=now)
    assert len(d_list) == 1
    assert d_list[0].state == "waiting_for_slot"
    assert "unsupported" in d_list[0].last_error.lower()

    # Dispatch: unsupported media must NOT make remote calls
    mock_runner = MagicMock()
    processed = dispatch_due_deliveries(db_session, now_utc=now, runner_client=mock_runner)
    assert len(processed) == 0
    assert mock_runner.publish_delivery.call_count == 0


# ── Test 9: publish_job integration & legacy fallback ─────────────────────────


def test_publish_job_with_destinations_idempotent(db_session, monkeypatch):
    fanpage, dest = _create_project_and_dest(
        db_session, kind="facebook_page", mode="repliz", repliz_id="page_repliz_1"
    )

    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.discussion,
        design_image_url="https://cdn.example.com/news.png",
        ai_generated_caption="Breaking story",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.commit()

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.return_value = {"id": "sched_pub_123"}
    monkeypatch.setattr(
        "app.services.social_delivery_dispatch.get_repliz_client_from_db",
        lambda db: mock_repliz,
    )
    Session = sessionmaker(bind=db_session.bind)
    monkeypatch.setattr("app.tasks.publisher.SessionLocal", Session)
    monkeypatch.setattr("app.database.SessionLocal", Session)

    # First call: publishes successfully
    publish_job(job.id)

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).first()
    assert j.status == PublishJobStatus.published
    assert j.repliz_schedule_id == "sched_pub_123"

    # Second call (duplicate/retry): atomic claim fails or no-ops, does not duplicate remote schedule
    publish_job(job.id)
    assert mock_repliz.create_image_schedule.call_count == 1
    assert db_session.query(PublishDelivery).filter_by(publish_job_id=job.id).count() == 1


def test_publish_job_legacy_fallback_no_destinations(db_session, monkeypatch):
    # Fanpage with NO destinations
    fanpage = TargetFanpage(
        name="Legacy Page",
        repliz_account_id="legacy_acc_1",
        is_active=True,
    )
    db_session.add(fanpage)
    db_session.commit()

    job = PublishJob(
        fanpage_id=fanpage.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/news.png",
        ai_generated_caption="Legacy story",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.commit()

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.return_value = {"id": "legacy_sched_99"}
    monkeypatch.setattr("app.services.repliz_client.get_repliz_client_from_db", lambda db: mock_repliz)
    monkeypatch.setattr("app.tasks.publisher._next_schedule_at", lambda db, fid, **kw: datetime(2026, 10, 15, 12, 0, 0))

    Session = sessionmaker(bind=db_session.bind)
    monkeypatch.setattr("app.tasks.publisher.SessionLocal", Session)
    monkeypatch.setattr("app.database.SessionLocal", Session)

    publish_job(job.id)

    db_session.expire_all()
    j = db_session.query(PublishJob).filter_by(id=job.id).first()
    assert j.status == PublishJobStatus.published
    assert j.repliz_schedule_id == "legacy_sched_99"
    # No delivery ledger rows created for legacy
    assert db_session.query(PublishDelivery).filter_by(publish_job_id=job.id).count() == 0


# ── Test 10: Fail closed on unverified assignment or identity mismatch ────────


def test_phone_dispatch_waits_when_assignment_not_verified(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    # Unverified assignment: verified_at is None
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=None,
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "waiting_for_device"
    assert "no eligible and available" in (res.last_error or "").lower()
    assert mock_runner.publish_delivery.call_count == 0


def test_phone_dispatch_waits_when_profile_proof_mismatch(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    # Wrong verified_profile_actor_id on assignment
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="wrong_actor_id",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "waiting_for_device"
    assert mock_runner.publish_delivery.call_count == 0

    # Also test mismatched verified_target_actor_id on assignment
    assign.verified_profile_actor_id = "actor_p1"
    assign.verified_target_actor_id = "wrong_target_actor"
    db_session.commit()

    res2 = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)
    assert res2.state == "waiting_for_device"
    assert mock_runner.publish_delivery.call_count == 0

    # Also test missing verified_target_actor_id on assignment
    assign.verified_target_actor_id = None
    db_session.commit()

    res3 = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)
    assert res3.state == "waiting_for_device"
    assert mock_runner.publish_delivery.call_count == 0


def test_phone_dispatch_waits_when_page_proof_or_parent_missing(db_session):
    # 1. Page without parent profile cannot be leased
    fanpage, page_dest_no_parent = _create_project_and_dest(
        db_session,
        kind="facebook_page",
        mode="phone",
        repliz_id=None,
        actor_id="page_act_1",
        parent_profile_id=None,
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="parent_act_1")
    assign_no_parent = DestinationDeviceAssignment(
        destination_id=page_dest_no_parent.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="parent_act_1",
        verified_target_actor_id="page_act_1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign_no_parent)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job1, deliv1 = _create_job_and_delivery(
        db_session, fanpage, page_dest_no_parent, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    res1 = dispatch_phone_delivery(db_session, deliv1, runner_client=mock_runner, now_utc=now)
    assert res1.state == "waiting_for_device"
    assert mock_runner.publish_delivery.call_count == 0

    # 2. Page with parent profile but mismatched target proof
    parent_fanpage, parent_prof = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="parent_act_1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_fanpage, page_dest = _create_project_and_dest(
        db_session,
        name="Page Dest",
        kind="facebook_page",
        mode="phone",
        repliz_id=None,
        actor_id="page_act_1",
        parent_profile_id=parent_prof.id,
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    # Assignment missing verified_target_actor_id (or wrong target proof)
    assign_page = DestinationDeviceAssignment(
        destination_id=page_dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="parent_act_1",
        verified_target_actor_id="wrong_page_id",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign_page)
    db_session.commit()

    job2, deliv2 = _create_job_and_delivery(
        db_session, page_fanpage, page_dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )
    res2 = dispatch_phone_delivery(db_session, deliv2, runner_client=mock_runner, now_utc=now)
    assert res2.state == "waiting_for_device"
    assert mock_runner.publish_delivery.call_count == 0


# ── Test 11: Uncertain on missing post ID or actor mismatch ───────────────────


def test_phone_dispatch_uncertain_on_missing_facebook_post_id(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    # Runner reports published but post_id is empty/whitespace
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="   ",
        actor_id="actor_p1",
        permalink="https://fb.com/none",
    )

    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "uncertain"
    assert res.uncertain_since is not None
    assert "missing" in (res.last_error or "").lower()

    # Event recorded
    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="uncertain")
        .first()
    )
    assert event is not None
    assert event.details.get("reason") == "missing_facebook_post_id"

    # Must never be retryable as a fresh post
    processed = dispatch_due_deliveries(db_session, now_utc=now, runner_client=mock_runner)
    assert len(processed) == 0


def test_phone_dispatch_uncertain_on_actor_id_mismatch(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    # Runner reports published but actor_id does not match destination
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post_real_123",
        actor_id="different_actor_456",
        permalink="https://fb.com/post_real_123",
    )

    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "uncertain"
    assert res.uncertain_since is not None
    assert "mismatch" in (res.last_error or "").lower()

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="uncertain")
        .first()
    )
    assert event is not None
    assert event.details.get("reason") == "actor_id_mismatch"

    # Must never be retryable as a fresh post
    processed = dispatch_due_deliveries(db_session, now_utc=now, runner_client=mock_runner)
    assert len(processed) == 0


# ── Test 12: Uncertain on Repliz response without external schedule ID ────────


def test_repliz_dispatch_uncertain_without_schedule_id(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session, kind="facebook_page", mode="repliz", repliz_id="page_repliz_99"
    )
    job, delivery = _create_job_and_delivery(db_session, fanpage, dest, intended_method="repliz")

    mock_repliz = MagicMock()
    # Successful HTTP response but empty / missing schedule ID
    mock_repliz.create_image_schedule.return_value = {"status": "ok", "message": "created"}

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    res = dispatch_repliz_delivery(db_session, delivery, repliz_client=mock_repliz, now_utc=now)

    assert res.state == "uncertain"
    assert res.uncertain_since is not None
    assert res.actual_method is None
    assert res.published_at is None
    assert res.repliz_schedule_id is None
    assert res.state != "repliz_scheduled"

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="uncertain")
        .first()
    )
    assert event is not None
    assert event.details.get("reason") == "missing_repliz_schedule_id"


# ── Test 13: Sanitize generic exception handling (no secret leakage) ──────────


def test_dispatch_sanitizes_generic_exceptions_no_secret_leak(db_session):
    sentinel_secret = "SECRET_TOKEN_DO_NOT_EXPOSE_xyz987"

    # 1. Repliz generic exception
    fanpage_r, dest_r = _create_project_and_dest(
        db_session, kind="facebook_page", mode="repliz", repliz_id="repliz_sec_1"
    )
    job_r, deliv_r = _create_job_and_delivery(db_session, fanpage_r, dest_r, intended_method="repliz")

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.side_effect = RuntimeError(
        f"Internal connection leak: Bearer {sentinel_secret}"
    )

    res_r = dispatch_repliz_delivery(db_session, deliv_r, repliz_client=mock_repliz)
    assert res_r.state == "uncertain"
    assert sentinel_secret not in (res_r.last_error or "")

    events_r = db_session.query(DeliveryEvent).filter_by(delivery_id=deliv_r.id).all()
    for ev in events_r:
        assert sentinel_secret not in json.dumps(ev.details)

    # 2. Phone generic exception
    fanpage_p, dest_p = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_sec_1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_sec_1")
    assign = DestinationDeviceAssignment(
        destination_id=dest_p.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_sec_1",
        verified_target_actor_id="actor_sec_1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    job_p, deliv_p = _create_job_and_delivery(db_session, fanpage_p, dest_p, intended_method="phone")

    mock_runner = MagicMock()
    mock_runner.publish_delivery.side_effect = ValueError(
        f"Device runner error with token: {sentinel_secret}"
    )

    res_p = dispatch_phone_delivery(db_session, deliv_p, runner_client=mock_runner)
    assert res_p.state == "uncertain"
    assert sentinel_secret not in (res_p.last_error or "")

    events_p = db_session.query(DeliveryEvent).filter_by(delivery_id=deliv_p.id).all()
    for ev in events_p:
        assert sentinel_secret not in json.dumps(ev.details)


# ── Test 14: Device lease contention uniqueness race safe unavailable ─────────


def test_lease_contention_uniqueness_race_safe_unavailable(db_session, monkeypatch):
    """Verify that IntegrityError on lease unique primary key does not escape.

    PostgreSQL uses `SELECT ... FOR UPDATE` row lock on PhoneDevice before checking/updating
    the DeviceLease to serialize concurrent workers. If a uniqueness race on
    `device_leases.device_id` occurs under contention, `select_and_lease_phone_device` catches
    the IntegrityError, rolls back the transaction, and safely treats the device as unavailable
    without crashing or making remote calls.
    """
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    # Simulate IntegrityError on db.commit during lease creation
    orig_commit = db_session.commit

    def commit_with_race():
        raise IntegrityError("UNIQUE constraint failed: device_leases.device_id", None, None)

    monkeypatch.setattr(db_session, "commit", commit_with_race)

    leased_dev, err = select_and_lease_phone_device(db_session, delivery, now_utc=now)
    assert leased_dev is None
    assert "no eligible and available" in (err or "").lower()

    # Restore commit and test dispatch_phone_delivery behavior under race
    monkeypatch.setattr(db_session, "commit", orig_commit)

    mock_runner = MagicMock()
    # Monkeypatch select_and_lease_phone_device to simulate race inside dispatch
    monkeypatch.setattr(
        "app.services.social_delivery_dispatch.select_and_lease_phone_device",
        lambda *args, **kw: (None, "No eligible and available phone device could be leased"),
    )

    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)
    assert res.state == "waiting_for_device"
    assert mock_runner.publish_delivery.call_count == 0


# ── Test 15: Device recovery recovers device and allows publish ────────────────


def test_recovery_device_recovers_and_allows_publish(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    # Device initially in maintenance
    dev = _create_phone_device(db_session, actor_id="actor_p1", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    mock_runner.recover_device.return_value = PhoneRecoveryResult(
        status="resolved",
        state="resolved",
        next_state="ready",
        serial=dev.adb_serial,
        check_count=1,
        hermes_attempt_count=0,
    )
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post_recovered_1",
        permalink="https://fb.com/post_recovered_1",
        actor_id="actor_p1",
    )

    res = dispatch_phone_delivery(
        db_session,
        delivery,
        runner_client=mock_runner,
        now_utc=now,
    )

    assert res.state == "published"
    assert res.facebook_post_id == "post_recovered_1"
    assert dev.status == "available"

    incident = (
        db_session.query(RecoveryIncident)
        .filter_by(device_id=dev.id)
        .first()
    )
    assert incident is not None
    assert incident.state == "resolved"
    assert incident.resolved_at is not None

    event_resolved = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="recovery_resolved")
        .first()
    )
    assert event_resolved is not None
    assert event_resolved.details.get("status") == "resolved"


# ── Test 16: Exhausted recovery persists incident and events ──────────────────


def test_recovery_exhausted_persists_incident_and_events(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    mock_runner.recover_device.return_value = PhoneRecoveryResult(
        status="needs_human",
        state="needs_human",
        next_state="needs_human",
        serial=dev.adb_serial,
        check_count=2,
        hermes_attempt_count=1,
        error="hermes healing failed: timeout",
        notification_sent=True,
    )

    incident = execute_device_recovery(
        db_session,
        delivery,
        runner_client=mock_runner,
        now_utc=now,
    )

    assert incident is not None
    assert incident.state == "needs_human"
    assert incident.check_count == 2
    assert incident.hermes_attempt_count == 1
    assert "timeout" in incident.last_error

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="recovery_needs_human")
        .first()
    )
    assert event is not None
    assert event.details.get("owner_notified") is True
    assert event.details.get("check_count") == 2
    assert event.details.get("hermes_attempt_count") == 1


# ── Test 17: Larger repair approval and 15-minute silence timing ──────────────


def test_recovery_larger_repair_approval_and_silence_timing(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    t0 = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=t0.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    # Step 1: Initial call at T0 enters awaiting_approval with 15-min window
    deadline_iso = (t0 + timedelta(minutes=15)).isoformat()
    mock_runner.recover_device.return_value = PhoneRecoveryResult(
        status="awaiting_approval",
        state="awaiting_approval",
        next_state="awaiting_approval",
        serial=dev.adb_serial,
        recovery_class="larger_repair",
        reason_code="system_unresponsive",
        check_count=1,
        hermes_attempt_count=0,
        owner_notified_at=t0.isoformat(),
        auto_approve_after=deadline_iso,
        notification_sent=True,
    )

    inc1 = execute_device_recovery(
        db_session,
        delivery,
        runner_client=mock_runner,
        recovery_class="larger_repair",
        now_utc=t0,
    )
    assert inc1.state == "awaiting_approval"
    assert inc1.auto_approve_after is not None

    # Step 2: At T0 + 10m (silence window active), dispatch honors window
    t_10m = t0 + timedelta(minutes=10)
    mock_runner.recover_device.reset_mock()

    inc2 = execute_device_recovery(
        db_session,
        delivery,
        runner_client=mock_runner,
        now_utc=t_10m,
    )
    assert inc2.state == "awaiting_approval"
    # Runner is not invoked during active silence window
    assert mock_runner.recover_device.call_count == 0

    # Step 3: At T0 + 16m (past 15-minute deadline), silence expires and auto-approves
    t_16m = t0 + timedelta(minutes=16)
    mock_runner.recover_device.return_value = PhoneRecoveryResult(
        status="resolved",
        state="resolved",
        next_state="ready",
        serial=dev.adb_serial,
        recovery_class="larger_repair",
        check_count=2,
        hermes_attempt_count=1,
    )

    inc3 = execute_device_recovery(
        db_session,
        delivery,
        runner_client=mock_runner,
        recovery_class="larger_repair",
        now_utc=t_16m,
    )
    assert inc3.state == "resolved"
    assert mock_runner.recover_device.call_count == 1
    assert dev.status == "available"


# ── Test 18: Pre-existing owner decision respected ────────────────────────────


def test_recovery_pre_existing_owner_decision_respected(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    # 1. Owner rejected: blocks larger repair, marks needs_human immediately
    mock_runner_rej = MagicMock()
    mock_runner_rej.recover_device.return_value = PhoneRecoveryResult(
        status="needs_human",
        state="needs_human",
        next_state="needs_human",
        serial=dev.adb_serial,
        owner_decision="rejected",
        check_count=1,
        hermes_attempt_count=0,
        error="larger_repair_rejected_by_owner",
    )

    inc_rej = execute_device_recovery(
        db_session,
        delivery,
        runner_client=mock_runner_rej,
        recovery_class="larger_repair",
        owner_decision="rejected",
        now_utc=now,
    )
    assert inc_rej.state == "needs_human"
    assert inc_rej.owner_decision == "rejected"
    assert inc_rej.hermes_attempt_count == 0

    # 2. Owner approved: proceeds immediately to Hermes repair
    dev2 = _create_phone_device(db_session, name="Phone 2", actor_id="actor_p2", status="maintenance")
    assign2 = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev2.id,
        priority=1,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign2)
    db_session.commit()

    mock_runner_appr = MagicMock()
    mock_runner_appr.recover_device.return_value = PhoneRecoveryResult(
        status="resolved",
        state="resolved",
        next_state="ready",
        serial=dev2.adb_serial,
        owner_decision="approved",
        check_count=2,
        hermes_attempt_count=1,
    )

    inc_appr = execute_device_recovery(
        db_session,
        delivery,
        device=dev2,
        runner_client=mock_runner_appr,
        recovery_class="larger_repair",
        owner_decision="approved",
        now_utc=now,
    )
    assert inc_appr.state == "resolved"
    assert inc_appr.owner_decision == "approved"
    assert dev2.status == "available"


# ── Test 19: Page fallback to Repliz when all gates met ───────────────────────


def test_page_fallback_to_repliz_when_all_gates_met(db_session):
    # Setup parent profile & child page
    parent_fanpage, parent_prof = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="parent_act",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_fanpage, page_dest = _create_project_and_dest(
        db_session,
        name="Fallback Page",
        kind="facebook_page",
        mode="phone",
        repliz_id="repliz_fb_page_123",
        actor_id="page_act_99",
        parent_profile_id=parent_prof.id,
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="parent_act", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=page_dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="parent_act",
        verified_target_actor_id="page_act_99",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, page_fanpage, page_dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    # Exhausted recovery incident: check_count=2, state=needs_human
    incident = RecoveryIncident(
        device_id=dev.id,
        delivery_id=delivery.id,
        state="needs_human",
        reason_code="device_offline",
        check_count=2,
        hermes_attempt_count=1,
        last_error="device_unreachable_repeated",
    )
    db_session.add(incident)
    db_session.commit()

    mock_repliz = MagicMock()
    mock_repliz.create_image_schedule.return_value = {"id": "sched_repliz_fallback_77"}

    mock_subproc = MagicMock()
    mock_subproc.return_value = MagicMock(returncode=0, stdout="sent", stderr="")

    res = evaluate_and_execute_fallback(
        db_session,
        delivery,
        incident=incident,
        repliz_client=mock_repliz,
        subprocess_runner=mock_subproc,
        now_utc=now,
    )

    assert res.state == "repliz_scheduled"
    assert res.intended_method == "repliz"
    assert res.actual_method == "repliz"
    assert res.repliz_schedule_id == "sched_repliz_fallback_77"

    # DeliveryEvents must include fallback_to_repliz and owner_notification_sent
    events = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id)
        .all()
    )
    event_types = [e.event_type for e in events]
    assert "fallback_to_repliz" in event_types
    assert "owner_notification_sent" in event_types
    assert "repliz_scheduled" in event_types

    # Subprocess runner was called for Hermes owner notices
    assert mock_subproc.call_count >= 1
    call_args = mock_subproc.call_args_list[0][0][0]
    assert call_args[:4] == ["hermes", "send", "-t", "telegram"]

    # OperationalNotification row persisted
    notif = db_session.query(OperationalNotification).filter_by(dedupe_key=f"fallback:{delivery.id}").first()
    assert notif is not None
    assert notif.state == "sent"


# ── Test 20: Profile never falls back to Repliz ───────────────────────────────


def test_profile_never_falls_back_to_repliz(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    incident = RecoveryIncident(
        device_id=dev.id,
        delivery_id=delivery.id,
        state="needs_human",
        reason_code="device_offline",
        check_count=2,
        hermes_attempt_count=1,
    )
    db_session.add(incident)
    db_session.commit()

    mock_repliz = MagicMock()
    mock_runner = MagicMock()

    res = evaluate_and_execute_fallback(
        db_session,
        delivery,
        incident=incident,
        repliz_client=mock_repliz,
        now_utc=now,
    )

    # Must stay waiting; never call Repliz
    assert res.state != "repliz_scheduled"
    assert res.intended_method == "phone"
    assert mock_repliz.create_image_schedule.call_count == 0

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="fallback_skipped")
        .first()
    )
    assert event is not None
    assert event.details.get("reason") == "profile_fallback_forbidden"


# ── Test 21: Unsupported media waits and never falls back ─────────────────────


def test_unsupported_media_waits_never_falls_back(db_session):
    parent_fanpage, parent_prof = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="parent_act",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_fanpage, page_dest = _create_project_and_dest(
        db_session,
        name="Media Page",
        kind="facebook_page",
        mode="phone",
        repliz_id="repliz_media_page",
        actor_id="page_act_media",
        parent_profile_id=parent_prof.id,
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    # Page only supports images
    page_dest.supported_media = ["image"]
    dev = _create_phone_device(db_session, actor_id="parent_act", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=page_dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="parent_act",
        verified_target_actor_id="page_act_media",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    # Delivery has video media kind
    job, delivery = _create_job_and_delivery(
        db_session,
        page_fanpage,
        page_dest,
        intended_method="phone",
        scheduled_for=now.replace(tzinfo=None),
        media_kind="video",
    )

    incident = RecoveryIncident(
        device_id=dev.id,
        delivery_id=delivery.id,
        state="needs_human",
        reason_code="device_offline",
        check_count=2,
        hermes_attempt_count=1,
    )
    db_session.add(incident)
    db_session.commit()

    mock_repliz = MagicMock()
    res = evaluate_and_execute_fallback(
        db_session,
        delivery,
        incident=incident,
        repliz_client=mock_repliz,
        now_utc=now,
    )

    # Must wait for slot, never Repliz
    assert res.state == "waiting_for_slot"
    assert "unsupported" in (res.last_error or "").lower()
    assert mock_repliz.create_video_schedule.call_count == 0

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="fallback_skipped")
        .first()
    )
    assert event is not None
    assert event.details.get("reason") == "unsupported_media"


# ── Test 22: Uncertain delivery never retried or fallen back ──────────────────


def test_uncertain_delivery_never_retried_or_fallen_back(db_session):
    parent_fanpage, parent_prof = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="parent_act",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_fanpage, page_dest = _create_project_and_dest(
        db_session,
        name="Uncertain Page",
        kind="facebook_page",
        mode="phone",
        repliz_id="repliz_unc_page",
        actor_id="page_act_unc",
        parent_profile_id=parent_prof.id,
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, page_fanpage, page_dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    # Mark delivery uncertain
    delivery.state = "uncertain"
    delivery.uncertain_since = now.replace(tzinfo=None)
    db_session.commit()

    # 1. Absence verification returns False
    assert reconcile_and_verify_absence(db_session, delivery) is False

    # 2. Claim fails
    claimed, _ = claim_delivery_for_dispatch(
        db_session, delivery.id, eligible_states=["scheduled"], now_utc=now
    )
    assert claimed is None

    # 3. Fallback is skipped
    mock_repliz = MagicMock()
    res = evaluate_and_execute_fallback(
        db_session,
        delivery,
        repliz_client=mock_repliz,
        now_utc=now,
    )
    assert res.state == "uncertain"
    assert mock_repliz.create_image_schedule.call_count == 0

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="fallback_skipped")
        .first()
    )
    assert event is not None
    assert event.details.get("reason") == "uncertain_delivery_forbidden"


# ── Test 23: Secret sanitization in recovery and events ───────────────────────


def test_sanitization_in_recovery_and_events(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1", status="maintenance")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    secret = "SUPER_SECRET_TOKEN_XYZ_123"
    mock_runner = MagicMock()
    mock_runner.recover_device.return_value = PhoneRecoveryResult(
        status="needs_human",
        state="needs_human",
        next_state="needs_human",
        serial=dev.adb_serial,
        error=f"Hermes failed: Bearer {secret} password={secret}",
    )

    inc = execute_device_recovery(
        db_session,
        delivery,
        runner_client=mock_runner,
        now_utc=now,
    )

    assert secret not in inc.last_error
    assert "[REDACTED]" in inc.last_error

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="recovery_needs_human")
        .first()
    )
    assert secret not in str(event.details)
    assert "[REDACTED]" in str(event.details)

    # Test notify_owner_via_hermes sanitizes message sent to subprocess
    called_cmds = []

    def fake_subproc(cmd, **kwargs):
        called_cmds.append(cmd)
        return MagicMock(returncode=0, stdout="sent", stderr="")

    raw_msg = f"Alert device failure: token={secret}"
    ok, detail = notify_owner_via_hermes(
        raw_msg,
        subprocess_runner=fake_subproc,
        dedupe_key=f"sanitize_test_{delivery.id}",
        db=db_session,
    )
    assert ok is True
    assert len(called_cmds) == 1
    sent_argv = " ".join(called_cmds[0])
    assert secret not in sent_argv
    assert "[REDACTED]" in sent_argv


def test_shared_device_two_verified_roots_dispatch_and_contention(db_session):
    """Two root destinations share one calibrated phone whose default is Root A.

    Page B (under Root B) can lease and dispatch on the device.
    Contention locks out Page A while Page B holds lease.
    Releasing lease allows Page A to acquire.
    Unverified or wrong-root assignments are rejected.
    """
    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)

    # 1. Calibrated available device with default root A
    dev = _create_phone_device(db_session, name="Shared Phone", actor_id="actor_root_a", status="available")

    # 2. Hierarchy Root A -> Page A
    fanpage_a, root_a = _create_project_and_dest(
        db_session,
        name="Project A",
        kind="facebook_profile",
        mode="phone",
        repliz_id="acc_shared_a",
        actor_id="actor_root_a",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_a = SocialDestination(
        content_project_id=fanpage_a.id,
        kind="facebook_page",
        name="Page Dest A",
        mode="phone",
        facebook_actor_id="actor_page_a",
        parent_profile_id=root_a.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(page_a)

    # 3. Hierarchy Root B -> Page B
    fanpage_b, root_b = _create_project_and_dest(
        db_session,
        name="Project B",
        kind="facebook_profile",
        mode="phone",
        repliz_id="acc_shared_b",
        actor_id="actor_root_b",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_b = SocialDestination(
        content_project_id=fanpage_b.id,
        kind="facebook_page",
        name="Page Dest B",
        mode="phone",
        facebook_actor_id="actor_page_b",
        parent_profile_id=root_b.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(page_b)

    # 4. Active, verified assignments for both pages on dev
    asgn_a = DestinationDeviceAssignment(
        destination_id=page_a.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_root_a",
        verified_target_actor_id="actor_page_a",
        verified_at=now.replace(tzinfo=None),
    )
    asgn_b = DestinationDeviceAssignment(
        destination_id=page_b.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_root_b",
        verified_target_actor_id="actor_page_b",
        verified_at=now.replace(tzinfo=None),
    )
    db_session.add_all([asgn_a, asgn_b])
    db_session.commit()

    job_a, deliv_a = _create_job_and_delivery(db_session, fanpage_a, page_a, intended_method="phone")
    job_b, deliv_b = _create_job_and_delivery(db_session, fanpage_b, page_b, intended_method="phone")

    # 5. Delivery B (under Root B) leases dev successfully, despite dev default being Root A
    leased_dev_b, err_b = select_and_lease_phone_device(db_session, deliv_b, now_utc=now)
    assert leased_dev_b is not None
    assert leased_dev_b.id == dev.id
    assert err_b is None

    # 6. Concurrent attempt by Delivery A to lease dev fails due to active lease contention
    leased_dev_a_blocked, err_a_blocked = select_and_lease_phone_device(db_session, deliv_a, now_utc=now)
    assert leased_dev_a_blocked is None
    assert "no eligible and available" in (err_a_blocked or "").lower()

    # 7. Delivery B completes and releases lease
    release_device_lease(db_session, dev.id, deliv_b.id)

    # 8. Delivery A can now lease dev
    leased_dev_a, err_a = select_and_lease_phone_device(db_session, deliv_a, now_utc=now)
    assert leased_dev_a is not None
    assert leased_dev_a.id == dev.id
    release_device_lease(db_session, dev.id, deliv_a.id)

    # 9. Negative checks: unverified assignment for Page B rejected
    asgn_b.verified_at = None
    db_session.commit()
    leased_unver, _ = select_and_lease_phone_device(db_session, deliv_b, now_utc=now)
    assert leased_unver is None

    # 10. Mismatched root ID rejected
    asgn_b.verified_at = now.replace(tzinfo=None)
    asgn_b.verified_profile_actor_id = "wrong_root_actor"
    db_session.commit()
    leased_wrong_root, _ = select_and_lease_phone_device(db_session, deliv_b, now_utc=now)
    assert leased_wrong_root is None

    # 11. Mismatched target ID rejected
    asgn_b.verified_profile_actor_id = "actor_root_b"
    asgn_b.verified_target_actor_id = "wrong_target_actor"
    db_session.commit()
    leased_wrong_target, _ = select_and_lease_phone_device(db_session, deliv_b, now_utc=now)
    assert leased_wrong_target is None

    # 12. Invalid parent kind rejected
    asgn_b.verified_target_actor_id = "actor_page_b"
    root_b.kind = "facebook_page"
    db_session.commit()
    leased_bad_parent, _ = select_and_lease_phone_device(db_session, deliv_b, now_utc=now)
    assert leased_bad_parent is None


# ── Test 20: HTTP 200 actor ID validation regression (Profile & Page) ────────


@pytest.mark.parametrize(
    "payload_actor,is_missing,should_confirm",
    [
        (None, True, False),            # missing actor_id key
        (None, False, False),           # explicit "actor_id": None
        ("", False, False),             # empty string
        ("   ", False, False),          # whitespace string
        ("wrong_actor_777", False, False),  # mismatched actor
        ("prof_actor_1", False, True),  # exact matching actor
    ],
)
def test_phone_dispatch_http200_profile_actor_validation_regression(
    db_session, payload_actor, is_missing, should_confirm
):
    fanpage, dest = _create_project_and_dest(
        db_session,
        name="Profile Project",
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="prof_actor_1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="prof_actor_1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="prof_actor_1",
        verified_target_actor_id="prof_actor_1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    fake_response = {
        "status": "published",
        "facebook_post_id": "post_prof_999",
        "permalink": "https://fb.com/post_prof_999",
        "publish_may_have_started": True,
    }
    if not is_missing:
        fake_response["actor_id"] = payload_actor

    mock_resp = httpx.Response(200, json=fake_response)
    client = PhoneRunnerClient(base_url="http://127.0.0.1:8765", token="test-token")

    with patch("httpx.Client.post", return_value=mock_resp):
        res = dispatch_phone_delivery(db_session, delivery, runner_client=client, now_utc=now)

    if should_confirm:
        assert res.state == "published"
        assert res.actual_method == "phone"
        assert res.facebook_post_id == "post_prof_999"
        assert res.last_error is None
        event = (
            db_session.query(DeliveryEvent)
            .filter_by(delivery_id=delivery.id, event_type="published")
            .first()
        )
        assert event is not None
        assert event.details.get("actor_id") == "prof_actor_1"
    else:
        assert res.state == "uncertain"
        assert res.uncertain_since is not None
        assert "mismatched actor id" in (res.last_error or "").lower()
        event = (
            db_session.query(DeliveryEvent)
            .filter_by(delivery_id=delivery.id, event_type="uncertain")
            .first()
        )
        assert event is not None
        assert event.details.get("reason") == "actor_id_mismatch"
        assert event.details.get("publish_may_have_started") is True

        # Must never be retryable as a fresh post
        processed = dispatch_due_deliveries(db_session, now_utc=now, runner_client=client)
        assert len(processed) == 0


@pytest.mark.parametrize(
    "payload_actor,is_missing,should_confirm",
    [
        (None, True, False),                # missing actor_id key
        (None, False, False),               # explicit "actor_id": None
        ("", False, False),                 # empty string
        ("   ", False, False),              # whitespace string
        ("page_root_1", False, False),      # parent root actor instead of page actor
        ("wrong_page_999", False, False),   # totally wrong actor
        ("page_actor_1", False, True),      # exact matching page actor
    ],
)
def test_phone_dispatch_http200_page_actor_validation_regression(
    db_session, payload_actor, is_missing, should_confirm
):
    fanpage, root_dest = _create_project_and_dest(
        db_session,
        name="Page Project Root",
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="page_root_1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    page_dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Target Page Dest",
        mode="phone",
        facebook_actor_id="page_actor_1",
        parent_profile_id=root_dest.id,
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        is_active=True,
    )
    db_session.add(page_dest)
    db_session.commit()

    dev = _create_phone_device(db_session, actor_id="page_root_1")
    assign = DestinationDeviceAssignment(
        destination_id=page_dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="page_root_1",
        verified_target_actor_id="page_actor_1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, page_dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    fake_response = {
        "status": "published",
        "facebook_post_id": "post_page_888",
        "permalink": "https://fb.com/post_page_888",
        "publish_may_have_started": True,
    }
    if not is_missing:
        fake_response["actor_id"] = payload_actor

    mock_resp = httpx.Response(200, json=fake_response)
    client = PhoneRunnerClient(base_url="http://127.0.0.1:8765", token="test-token")

    with patch("httpx.Client.post", return_value=mock_resp):
        res = dispatch_phone_delivery(db_session, delivery, runner_client=client, now_utc=now)

    if should_confirm:
        assert res.state == "published"
        assert res.actual_method == "phone"
        assert res.facebook_post_id == "post_page_888"
        assert res.last_error is None
        event = (
            db_session.query(DeliveryEvent)
            .filter_by(delivery_id=delivery.id, event_type="published")
            .first()
        )
        assert event is not None
        assert event.details.get("actor_id") == "page_actor_1"
    else:
        assert res.state == "uncertain"
        assert res.uncertain_since is not None
        assert "mismatched actor id" in (res.last_error or "").lower()
        event = (
            db_session.query(DeliveryEvent)
            .filter_by(delivery_id=delivery.id, event_type="uncertain")
            .first()
        )
        assert event is not None
        assert event.details.get("reason") == "actor_id_mismatch"
        assert event.details.get("publish_may_have_started") is True

        # Must never be retryable as a fresh post
        processed = dispatch_due_deliveries(db_session, now_utc=now, runner_client=client)
        assert len(processed) == 0


def test_phone_dispatch_failed_stores_stages_and_composed_last_error(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    stages = [
        {"stage": "wake", "passed": True, "detail": "screen awake"},
        {
            "stage": "root_url",
            "passed": False,
            "detail": "Operation timed out: deadline exceeded before root own profile check",
        },
        {"stage": "composer", "passed": False, "detail": "not reached"},
    ]
    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="failed",
        error_code="identity_unverified",
        stages=stages,
    )

    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "failed"
    assert (
        res.last_error
        == "identity_unverified: root_url: Operation timed out: deadline exceeded before root own profile check"
    )

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="failed")
        .first()
    )
    assert event is not None
    assert event.details.get("error_code") == "identity_unverified"
    assert event.details.get("stages") == stages


def test_phone_dispatch_failed_without_stages_omits_stages_in_event_details(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="failed",
        error_code="identity_unverified",
        stages=[],
    )

    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "failed"
    assert res.last_error == "identity_unverified"

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="failed")
        .first()
    )
    assert event is not None
    assert event.details.get("error_code") == "identity_unverified"
    assert "stages" not in event.details


def test_phone_dispatch_waiting_for_device_stores_stages_and_composed_last_error(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    stages = [{"stage": "portal", "passed": False, "detail": "accessibility service disabled"}]
    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="waiting_for_device",
        error_code="device_unready",
        stages=stages,
    )

    res = dispatch_phone_delivery(
        db_session,
        delivery,
        runner_client=mock_runner,
        now_utc=now,
        auto_recover=False,
    )

    assert res.state == "waiting_for_device"
    assert res.last_error == "device_unready: portal: accessibility service disabled"

    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="waiting_for_device")
        .first()
    )
    assert event is not None
    assert event.details.get("error_code") == "device_unready"
    assert event.details.get("stages") == stages


def test_phone_dispatch_uncertain_stores_stages_in_event_details(db_session):
    fanpage, dest = _create_project_and_dest(
        db_session,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db_session, actor_id="actor_p1")
    assign = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=dev.id,
        priority=0,
        is_active=True,
        verified_profile_actor_id="actor_p1",
        verified_target_actor_id="actor_p1",
        verified_at=datetime(2026, 1, 1, 0, 0, 0),
    )
    db_session.add(assign)
    db_session.commit()

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    job, delivery = _create_job_and_delivery(
        db_session, fanpage, dest, intended_method="phone", scheduled_for=now.replace(tzinfo=None)
    )

    stages = [
        {"stage": "composer", "passed": True, "detail": "ready"},
        {"stage": "post_submit", "passed": False, "detail": "read timeout waiting for post id"},
    ]
    mock_runner = MagicMock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="uncertain",
        publish_may_have_started=True,
        error_code="timeout_uncertain",
        stages=stages,
    )

    res = dispatch_phone_delivery(db_session, delivery, runner_client=mock_runner, now_utc=now)

    assert res.state == "uncertain"
    event = (
        db_session.query(DeliveryEvent)
        .filter_by(delivery_id=delivery.id, event_type="uncertain")
        .first()
    )
    assert event is not None
    assert event.details.get("stages") == stages


def test_compose_phone_last_error_sanitization_and_length_limit():
    stages = [
        {"stage": "wake", "passed": True, "detail": "ok"},
        {
            "stage": "auth_check",
            "passed": False,
            "detail": "Bearer my_super_secret_token_123 failed password='secret'",
        },
    ]
    err = _compose_phone_last_error("auth_failed", stages)
    assert "my_super_secret_token_123" not in err
    assert "[REDACTED]" in err
    assert "Bearer [REDACTED]" in err

    very_long_detail = "Z" * 350
    stages_long = [{"stage": "long_stage", "passed": False, "detail": very_long_detail}]
    err_long = _compose_phone_last_error("error_code", stages_long)
    assert len(err_long) <= 300
    assert err_long.endswith("...")
