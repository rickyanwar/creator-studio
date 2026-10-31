"""Tests for late phone post follow-up checks and Reel duration pre-check (TASK-PB).

Verifies:
1. uncertain 1 min ago -> not due, runner not called.
2. uncertain 3 min ago, 0 events -> due; found with matching actor -> published, post id set, published event with via.
3. not_found -> stays uncertain, post_link_followup event attempt=1; next call 1 min later is not due (needs +5 min).
4. busy -> no event recorded.
5. 4th attempt passes close_app_after=True; after it fails -> one alert, still uncertain; no 5th call.
6. found with a different actor -> stays uncertain, event status actor_mismatch.
7. delivery with facebook_post_id already set, or uncertain 50 min ago, or not phone -> ignored.
8. device lease is released even when the runner raises.
9. Reel check: video_duration_s 120 -> runner not called, delivery failed outcome with video_too_long; 60 -> normal path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

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
from app.services.phone_runner_client import PhonePublishResult
from app.services.social_delivery_dispatch import (
    dispatch_phone_delivery,
    follow_up_uncertain_phone_post,
)


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


def _setup_base_models(db_session):
    fanpage = TargetFanpage(
        id=1,
        name="Test Motorsport Page",
        is_active=True,
    )
    db_session.add(fanpage)

    destination = SocialDestination(
        id=10,
        content_project_id=1,
        kind="facebook_profile",
        name="Motorsport News FB",
        facebook_actor_id="100094901131361",
        parent_profile_id=None,
        is_active=True,
    )
    db_session.add(destination)

    device = PhoneDevice(
        id=1,
        name="Pixel 7 Test",
        adb_serial="100.64.96.50:5555",
        status="available",
        calibration_verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        app_card_version="1.0.0",
        screen_width=1080,
        screen_height=2400,
    )
    db_session.add(device)

    assignment = DestinationDeviceAssignment(
        destination_id=10,
        device_id=1,
        priority=0,
        is_active=True,
        verified_at=datetime.now(timezone.utc).replace(tzinfo=None),
        verified_profile_actor_id="100094901131361",
        verified_target_actor_id="100094901131361",
    )
    db_session.add(assignment)
    db_session.commit()
    return fanpage, destination, device


def _create_delivery(
    db_session,
    destination,
    device=None,
    state="uncertain",
    uncertain_since=None,
    facebook_post_id=None,
    media_kind="image",
    video_duration_s=None,
):
    job = PublishJob(
        fanpage_id=destination.content_project_id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.publishing,
        video_duration_s=video_duration_s,
    )
    db_session.add(job)
    db_session.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=destination.id,
        device_id=device.id if device else None,
        state=state,
        uncertain_since=uncertain_since,
        facebook_post_id=facebook_post_id,
        caption_snapshot="Full speed at the Grand Prix weekend",
        media_snapshot=["https://cdn.example.com/photo.jpg"],
        media_kind=media_kind,
        attempt_count=1,
    )
    db_session.add(delivery)
    db_session.commit()
    return job, delivery


def test_followup_uncertain_1min_ago_not_due(db_session):
    """1. uncertain 1 min ago -> not due, runner not called."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=1),
    )

    mock_client = MagicMock()
    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)

    assert outcome == "no_due_deliveries"
    mock_client.resolve_post.assert_not_called()
    assert delivery.state == "uncertain"


def test_followup_uncertain_3min_ago_due_found_published(db_session):
    """2. uncertain 3 min ago, 0 events -> due; found with matching actor -> published, post id set, published event with via."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=3),
    )

    mock_client = MagicMock()
    mock_client.resolve_post.return_value = {
        "status": "found",
        "facebook_post_id": "9988776655",
        "permalink": "https://www.facebook.com/100094901131361/posts/9988776655",
        "actor_id": destination.facebook_actor_id,
        "detail": "post visible on profile",
    }

    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)

    assert outcome == f"delivery_{delivery.id}_published"
    assert delivery.state == "published"
    assert delivery.actual_method == "phone"
    assert delivery.facebook_post_id == "9988776655"
    assert delivery.facebook_permalink == "https://www.facebook.com/100094901131361/posts/9988776655"
    assert delivery.published_at == naive_now
    assert delivery.last_error is None

    mock_client.resolve_post.assert_called_once()
    assert mock_client.resolve_post.call_args[1]["close_app_after"] is False

    events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).order_by(DeliveryEvent.id.asc()).all()
    assert len(events) == 2
    followup_ev, published_ev = events
    assert followup_ev.event_type == "post_link_followup"
    assert followup_ev.details["attempt"] == 1
    assert followup_ev.details["status"] == "found"

    assert published_ev.event_type == "published"
    assert published_ev.details["facebook_post_id"] == "9988776655"
    assert published_ev.details["via"] == "post_link_followup"
    assert published_ev.details["actual_method"] == "phone"

    assert db_session.query(DeviceLease).count() == 0


def test_followup_not_found_stays_uncertain_and_schedule(db_session):
    """3. not_found -> stays uncertain, post_link_followup event attempt=1; next call 1 min later is not due (needs +5 min)."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=3),
    )

    mock_client = MagicMock()
    mock_client.resolve_post.return_value = {
        "status": "not_found",
        "detail": "post link not on profile yet",
    }

    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)
    assert outcome == f"delivery_{delivery.id}_not_found"
    assert delivery.state == "uncertain"

    events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
    assert len(events) == 1
    assert events[0].event_type == "post_link_followup"
    assert events[0].details["attempt"] == 1
    assert events[0].details["status"] == "not_found"

    # Next call 1 min later: uncertain_since + 4m < +5m offset -> not due
    mock_client.resolve_post.reset_mock()
    now_plus_1 = now + timedelta(minutes=1)
    outcome2 = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now_plus_1)
    assert outcome2 == "no_due_deliveries"
    mock_client.resolve_post.assert_not_called()

    # Call at +5.5 min from uncertain_since -> due (offset is 5 min for attempt 2)
    now_due_2 = now + timedelta(minutes=2, seconds=35)
    outcome3 = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now_due_2)
    assert outcome3 == f"delivery_{delivery.id}_not_found"
    mock_client.resolve_post.assert_called_once()
    assert mock_client.resolve_post.call_args[1]["close_app_after"] is False

    events2 = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
    assert len(events2) == 2


def test_followup_busy_no_event_recorded(db_session):
    """4. busy -> no event recorded."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=3),
    )

    mock_client = MagicMock()
    mock_client.resolve_post.return_value = {"status": "busy"}

    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)
    assert outcome == f"delivery_{delivery.id}_busy"
    assert delivery.state == "uncertain"
    assert db_session.query(DeliveryEvent).count() == 0
    assert db_session.query(DeviceLease).count() == 0


def test_followup_4th_attempt_close_app_alert_no_5th_call(db_session):
    """5. 4th attempt passes close_app_after=True; after it fails -> one alert, still uncertain; no 5th call."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=22),
    )

    for i in range(1, 4):
        db_session.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="post_link_followup",
                details={"attempt": i, "status": "not_found", "detail": "retry"},
                created_at=naive_now - timedelta(minutes=22 - i * 5),
            )
        )
    db_session.commit()

    mock_client = MagicMock()
    mock_client.resolve_post.return_value = {
        "status": "not_found",
        "detail": "still missing after 20 minutes",
    }

    with patch("app.services.social_delivery_dispatch.notify_owner_via_hermes") as mock_notify:
        outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)

    assert outcome == f"delivery_{delivery.id}_not_found"
    assert delivery.state == "uncertain"

    # 4th attempt must pass close_app_after=True
    assert mock_client.resolve_post.call_args[1]["close_app_after"] is True

    # Owner alerted with exact verbatim message and dedupe key
    mock_notify.assert_called_once()
    alert_args = mock_notify.call_args[1]
    expected_msg = (
        f"Phone post for delivery {delivery.id} ({destination.name}) is still unconfirmed 20 minutes after SHARE. "
        f"The post link was not found on the profile. Check the profile and use Mark posted / Mark not posted. "
        f"It will not be posted again automatically."
    )
    assert alert_args["message"] == expected_msg
    assert alert_args["dedupe_key"] == f"phone_post_followup:{delivery.id}"

    # Verify no 5th call
    mock_client.resolve_post.reset_mock()
    later_now = now + timedelta(minutes=15)
    outcome_5th = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=later_now)
    assert outcome_5th == "no_due_deliveries"
    mock_client.resolve_post.assert_not_called()


def test_followup_found_different_actor_stays_uncertain(db_session):
    """6. found with a different actor -> stays uncertain, event status actor_mismatch."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=3),
    )

    mock_client = MagicMock()
    mock_client.resolve_post.return_value = {
        "status": "found",
        "facebook_post_id": "8877665544",
        "permalink": "https://www.facebook.com/wrongactor/posts/8877665544",
        "actor_id": "wrong_actor_999999",
        "detail": "found but wrong actor",
    }

    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)

    assert outcome == f"delivery_{delivery.id}_actor_mismatch"
    assert delivery.state == "uncertain"
    assert delivery.facebook_post_id is None

    events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
    assert len(events) == 1
    assert events[0].event_type == "post_link_followup"
    assert events[0].details["status"] == "actor_mismatch"
    assert events[0].details["actor_id"] == "wrong_actor_999999"


def test_followup_ignored_candidates(db_session):
    """7. delivery with facebook_post_id already set, or uncertain 50 min ago, or not phone -> ignored."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    # 1. Delivery with facebook_post_id already set
    _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=5),
        facebook_post_id="already_posted_id",
    )

    # 2. Delivery uncertain 50 min ago (> 45m cutoff)
    _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=50),
    )

    # 3. Non-phone delivery (device_id is None)
    _create_delivery(
        db_session,
        destination,
        device=None,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=5),
    )

    mock_client = MagicMock()
    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)

    assert outcome == "no_due_deliveries"
    mock_client.resolve_post.assert_not_called()


def test_followup_device_lease_released_when_runner_raises(db_session):
    """8. device lease is released even when the runner raises."""
    _, destination, device = _setup_base_models(db_session)
    now = datetime(2026, 10, 31, 12, 0, 0, tzinfo=timezone.utc)
    naive_now = now.replace(tzinfo=None)

    _, delivery = _create_delivery(
        db_session,
        destination,
        device=device,
        state="uncertain",
        uncertain_since=naive_now - timedelta(minutes=3),
    )

    mock_client = MagicMock()
    mock_client.resolve_post.side_effect = RuntimeError("Runner network crash")

    outcome = follow_up_uncertain_phone_post(db_session, runner_client=mock_client, now_utc=now)

    assert "delivery" in outcome
    assert delivery.state == "uncertain"
    # Lease must be completely released
    assert db_session.query(DeviceLease).count() == 0


def test_reel_duration_precheck(db_session):
    """9. Reel check: video_duration_s 120 -> runner not called, delivery failed outcome with video_too_long; 60 -> normal path."""
    _, destination, device = _setup_base_models(db_session)

    # 9a. Video duration 120s > 90s -> fails before leasing device
    job_120, deliv_120 = _create_delivery(
        db_session,
        destination,
        device=device,
        state="scheduled",
        media_kind="video",
        video_duration_s=120.0,
    )

    mock_runner = MagicMock()
    res_120 = dispatch_phone_delivery(db_session, deliv_120, runner_client=mock_runner)

    assert res_120.state == "failed"
    assert "video_too_long: 120 s > 90 s" in res_120.last_error
    mock_runner.publish_delivery.assert_not_called()
    assert db_session.query(DeviceLease).count() == 0

    events_120 = db_session.query(DeliveryEvent).filter_by(delivery_id=deliv_120.id).all()
    assert len(events_120) == 1
    assert events_120[0].event_type == "failed"
    assert events_120[0].details["error_code"] == "video_too_long: 120 s > 90 s"

    # 9b. Video duration 60s <= 90s -> proceeds to runner
    job_60, deliv_60 = _create_delivery(
        db_session,
        destination,
        device=device,
        state="scheduled",
        media_kind="video",
        video_duration_s=60.0,
    )

    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post_video_60",
        actor_id=destination.facebook_actor_id,
        permalink="https://facebook.com/reel/post_video_60",
    )

    res_60 = dispatch_phone_delivery(db_session, deliv_60, runner_client=mock_runner)
    assert res_60.state == "published"
    assert res_60.facebook_post_id == "post_video_60"
    mock_runner.publish_delivery.assert_called_once()

    # 9c. Video duration None -> normal path (no check)
    job_none, deliv_none = _create_delivery(
        db_session,
        destination,
        device=device,
        state="scheduled",
        media_kind="video",
        video_duration_s=None,
    )

    mock_runner.publish_delivery.reset_mock()
    mock_runner.publish_delivery.return_value = PhonePublishResult(
        status="published",
        facebook_post_id="post_video_none",
        actor_id=destination.facebook_actor_id,
        permalink="https://facebook.com/reel/post_video_none",
    )

    res_none = dispatch_phone_delivery(db_session, deliv_none, runner_client=mock_runner)
    assert res_none.state == "published"
    mock_runner.publish_delivery.assert_called_once()
