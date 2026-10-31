"""Retry cap for phone identity UI failures (identity_ui_unready).

The phone runner returns waiting_for_device / identity_ui_unready when it could not read the
Facebook identity screen (nothing was posted). The backend retries twice, then fails the delivery.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models.social_publishing import DeliveryEvent, DestinationDeviceAssignment
from app.services.social_delivery_dispatch import (
    IDENTITY_UI_MAX_RETRIES,
    PhonePublishResult,
    dispatch_phone_delivery,
)
from tests.test_social_delivery_dispatch import (  # noqa: F401  (db_session is a fixture)
    _create_job_and_delivery,
    _create_phone_device,
    _create_project_and_dest,
    db_session,
)

NOW = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
IDENTITY_STAGES = [
    {"stage": "identity_gate", "passed": False, "detail": "Identity proof verification failed: Copy profile link button not found after scroll"},
]


def _phone_delivery(db):
    fanpage, dest = _create_project_and_dest(
        db,
        kind="facebook_profile",
        mode="phone",
        repliz_id=None,
        actor_id="actor_p1",
        daily_target=1,
        phone_target=1,
        repliz_target=0,
    )
    dev = _create_phone_device(db, actor_id="actor_p1")
    db.add(
        DestinationDeviceAssignment(
            destination_id=dest.id,
            device_id=dev.id,
            priority=0,
            is_active=True,
            verified_profile_actor_id="actor_p1",
            verified_target_actor_id="actor_p1",
            verified_at=datetime(2026, 1, 1, 0, 0, 0),
        )
    )
    db.commit()
    _job, delivery = _create_job_and_delivery(
        db, fanpage, dest, intended_method="phone", scheduled_for=NOW.replace(tzinfo=None)
    )
    return delivery


def _dispatch(db, delivery, error_code):
    runner = MagicMock()
    runner.publish_delivery.return_value = PhonePublishResult(
        status="waiting_for_device",
        error_code=error_code,
        stages=IDENTITY_STAGES,
    )
    res = dispatch_phone_delivery(db, delivery, runner_client=runner, now_utc=NOW, auto_recover=False)
    # The scheduler picks a waiting delivery up again later; the next attempt starts from here.
    return res


def _events(db, delivery_id, event_type):
    return db.query(DeliveryEvent).filter_by(delivery_id=delivery_id, event_type=event_type).all()


def test_first_and_second_identity_ui_unready_wait_for_device(db_session):
    delivery = _phone_delivery(db_session)

    for attempt in range(IDENTITY_UI_MAX_RETRIES):
        res = _dispatch(db_session, delivery, "identity_ui_unready")
        assert res.state == "waiting_for_device", f"attempt {attempt + 1}"
        assert res.last_error.startswith("identity_ui_unready: identity_gate: ")

    waits = _events(db_session, delivery.id, "waiting_for_device")
    assert [e.details.get("error_code") for e in waits] == ["identity_ui_unready"] * IDENTITY_UI_MAX_RETRIES
    assert _events(db_session, delivery.id, "failed") == []


def test_third_identity_ui_unready_fails_delivery(db_session):
    delivery = _phone_delivery(db_session)
    for _ in range(IDENTITY_UI_MAX_RETRIES):
        _dispatch(db_session, delivery, "identity_ui_unready")

    res = _dispatch(db_session, delivery, "identity_ui_unready")

    assert res.state == "failed"
    assert res.last_error == "identity_unverified: identity could not be read after 3 tries"
    failed = _events(db_session, delivery.id, "failed")
    assert len(failed) == 1
    assert failed[0].details == {"error_code": "identity_unverified", "stages": IDENTITY_STAGES}
    # The capped attempt does not add another wait event.
    assert len(_events(db_session, delivery.id, "waiting_for_device")) == IDENTITY_UI_MAX_RETRIES


def test_cap_skips_auto_recovery(db_session, monkeypatch):
    delivery = _phone_delivery(db_session)
    for _ in range(IDENTITY_UI_MAX_RETRIES):
        _dispatch(db_session, delivery, "identity_ui_unready")

    recovery = MagicMock()
    monkeypatch.setattr("app.services.social_delivery_dispatch.execute_device_recovery", recovery)
    runner = MagicMock()
    runner.publish_delivery.return_value = PhonePublishResult(
        status="waiting_for_device", error_code="identity_ui_unready", stages=IDENTITY_STAGES
    )
    res = dispatch_phone_delivery(db_session, delivery, runner_client=runner, now_utc=NOW, auto_recover=True)

    assert res.state == "failed"
    recovery.assert_not_called()


def test_other_waiting_codes_never_hit_the_cap(db_session):
    delivery = _phone_delivery(db_session)

    for _ in range(IDENTITY_UI_MAX_RETRIES + 3):
        res = _dispatch(db_session, delivery, "vision_unavailable")
        assert res.state == "waiting_for_device"

    assert _events(db_session, delivery.id, "failed") == []


def test_other_waiting_codes_do_not_count_towards_the_cap(db_session):
    delivery = _phone_delivery(db_session)
    for _ in range(3):
        _dispatch(db_session, delivery, "vision_unavailable")
    _dispatch(db_session, delivery, "identity_ui_unready")

    res = _dispatch(db_session, delivery, "identity_ui_unready")

    assert res.state == "waiting_for_device"
