"""Calibrate endpoint and calibration fields in the Group Share accounts list (Celery task mocked)."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from app.models.fb_group_share import FbShareDelivery
from app.tasks import fb_share as fb_share_tasks
from backend.tests.test_fb_group_share_api import client  # noqa: F401  (fixture)
from backend.tests.test_fb_group_share_models import db_session  # noqa: F401  (fixture)

PATH = "/fanpages/1/group-share/accounts"


@pytest.fixture
def queued(monkeypatch):
    delay = Mock()
    monkeypatch.setattr(fb_share_tasks.calibrate_delivery, "delay", delay)
    return delay


def phone_delivery(db, **values):
    delivery = FbShareDelivery(**{"destination_id": 1, "mode": "phone_auto", "phone_device_id": 1, **values})
    db.add(delivery)
    db.commit()
    return delivery


def _utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def test_calibrate_queues_task_and_marks_pending(client, db_session, queued):
    delivery = phone_delivery(db_session, calibration={"fb_version": "500.0", "ok": True})
    result = client.post(f"{PATH}/1/calibrate")
    assert result.status_code == 202
    queued.assert_called_once_with(1)
    body = result.json()["delivery"]
    assert body["calibration_pending"] is True and body["calibration_fb_version"] == "500.0"
    db_session.refresh(delivery)
    assert delivery.calibration["fb_version"] == "500.0" and "requested_at" in delivery.calibration
    assert client.post(f"{PATH}/1/calibrate").status_code == 409
    queued.assert_called_once()


def test_stale_pending_request_can_be_retried(client, db_session, queued):
    phone_delivery(db_session, calibration={"requested_at": (_utc_now() - timedelta(minutes=21)).isoformat()})
    assert client.get(PATH).json()[0]["delivery"]["calibration_pending"] is False
    assert client.post(f"{PATH}/1/calibrate").status_code == 202


@pytest.mark.parametrize("values", [None, {"mode": "pic_whatsapp", "pic_name": "A", "pic_contact": "+62 812 3456 789"},
                                    {"phone_device_id": None}])
def test_calibrate_requires_phone_mode_and_device(client, db_session, queued, values):
    if values is not None:
        phone_delivery(db_session, **values)
    result = client.post(f"{PATH}/1/calibrate")
    assert result.status_code == 422 and "before calibrating" in result.text
    queued.assert_not_called()


def test_calibrate_scopes_brand_and_account(client, db_session, queued):
    assert client.post(f"{PATH}/3/calibrate").status_code == 404
    assert client.post("/fanpages/999/group-share/accounts/1/calibrate").status_code == 404
    queued.assert_not_called()


def test_queue_failure_restores_calibration(client, db_session, monkeypatch):
    delivery = phone_delivery(db_session, calibration={"fb_version": "500.0"})
    monkeypatch.setattr(fb_share_tasks.calibrate_delivery, "delay", Mock(side_effect=RuntimeError("broker down")))
    assert client.post(f"{PATH}/1/calibrate").status_code == 503
    db_session.refresh(delivery)
    assert delivery.calibration == {"fb_version": "500.0"}


def test_accounts_list_shows_calibration_result(client, db_session):
    checked = "2026-11-10T09:00:00"
    phone_delivery(db_session, calibrated_at=datetime(2026, 11, 10, 9), calibration={
        "fb_version": "500.0", "fb_version_code": "123", "android": "14", "screen": "1080x2400",
        "steps": [{"name": "composer", "ok": True, "detail": ""}], "calibrated_by": "rehearsal",
        "group_url": "https://www.facebook.com/groups/g1", "ok": True, "detail": "rehearsed", "checked_at": checked,
    })
    delivery = client.get(PATH).json()[0]["delivery"]
    assert delivery["calibration"]["steps"][0]["name"] == "composer"
    assert {key: delivery[key] for key in (
        "calibration_fb_version", "calibration_ok", "calibration_detail", "calibration_checked_at",
        "calibration_pending", "paused_reason")} == {
        "calibration_fb_version": "500.0", "calibration_ok": True, "calibration_detail": "rehearsed",
        "calibration_checked_at": checked, "calibration_pending": False, "paused_reason": None,
    }


def test_accounts_list_shows_pause_reason_and_last_busy_attempt(client, db_session):
    phone_delivery(db_session, calibration={
        "fb_version": "500.0", "ok": True, "paused_reason": "recalibrate: FB app changed 500.0 -> 501.0",
        "last_attempt": {"ok": False, "detail": "the phone was busy", "checked_at": "2026-11-10T10:00:00"},
    })
    delivery = client.get(PATH).json()[0]["delivery"]
    assert delivery["paused_reason"] == "recalibrate: FB app changed 500.0 -> 501.0"
    assert delivery["calibration_ok"] is False and delivery["calibration_detail"] == "the phone was busy"


def test_accounts_without_calibration_have_empty_fields(client, db_session):
    phone_delivery(db_session)
    delivery = client.get(PATH).json()[0]["delivery"]
    assert delivery["calibration"] is None and delivery["calibration_ok"] is None
    assert delivery["calibration_pending"] is False and delivery["calibration_fb_version"] is None


def test_phone_or_mode_change_refused_while_calibration_pending(client, db_session):
    from app.models.social_publishing import PhoneDevice

    db_session.add(PhoneDevice(id=2, name="Phone 2"))
    phone_delivery(db_session, calibration={"requested_at": _utc_now().isoformat()})
    same = {"mode": "phone_auto", "phone_device_id": 1}
    other_phone = {"mode": "phone_auto", "phone_device_id": 2}
    other_mode = {"mode": "pic_whatsapp", "pic_name": "A", "pic_contact": "+62 812 3456 789"}
    assert client.put(f"{PATH}/1", json=other_phone).status_code == 409
    assert client.put(f"{PATH}/1", json=other_mode).status_code == 409
    assert client.put(f"{PATH}/1", json=same).status_code == 200


def test_phone_change_allowed_once_calibration_is_not_pending(client, db_session):
    from app.models.social_publishing import PhoneDevice

    db_session.add(PhoneDevice(id=2, name="Phone 2"))
    delivery = phone_delivery(db_session, calibrated_at=datetime(2026, 11, 10, 9),
                              calibration={"fb_version": "500.0", "phone_device_id": 1})
    result = client.put(f"{PATH}/1", json={"mode": "phone_auto", "phone_device_id": 2})
    assert result.status_code == 200
    db_session.refresh(delivery)
    assert delivery.calibration is None and delivery.calibrated_at is None
