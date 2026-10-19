"""Tests for phone health watchdog task (P2).

Verifies:
1. healthy device → no status change, no alert.
2. unhealthy + recover fixes it → available at end, marker resolved, 0 alerts.
3. unhealthy + recover fails → maintenance, marker open, exactly 1 alert; second tick → still 1 alert total, recover not retried within 30 min.
4. recovers on a later tick → available, marker resolved, 1 "healthy again" alert.
5. owner-set maintenance without marker → untouched.
6. active lease → skipped (no probe call).
7. runner unreachable → status unchanged, no alert.
8. hermes send fails + telegram configured → Telegram fallback called once (mock httpx), token never in logs.
9. destination inactive / repliz-only → device not probed.
10. probe_device client method unit tests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

# Allow SQLite to adapt list/dict objects and compile PostgreSQL ARRAY and JSONB types
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.settings import Settings
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.encryption import encrypt
from app.services.phone_runner_client import PhoneRunnerClient
from app.tasks.phone_watchdog import check_phone_devices


@pytest.fixture
def db_session():
    """Create isolated in-memory SQLite database and session."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    orig_opn_constraints = list(OperationalNotification.__table__.constraints)
    OperationalNotification.__table__.constraints = {
        c for c in orig_opn_constraints
        if not (hasattr(c, "name") and c.name == "ck_operational_notifications_state")
    }

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    DeviceLease.__table__.create(engine)
    OperationalNotification.__table__.create(engine)
    Settings.__table__.create(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        sd_col.server_default = orig_sd
        OperationalNotification.__table__.constraints = set(orig_opn_constraints)


def _seed_device_and_dest(
    db,
    *,
    device_status: str = "available",
    dest_mode: str = "phone",
    dest_active: bool = True,
    assignment_active: bool = True,
    serial: str = "127.0.0.1:5555",
    name: str = "realme 7 Pro",
) -> tuple[PhoneDevice, SocialDestination]:
    fanpage = TargetFanpage(
        name="Motorsport News",
        is_active=True,
    )
    db.add(fanpage)
    db.commit()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Motorsport Page",
        mode=dest_mode,
        is_active=dest_active,
    )
    db.add(dest)
    db.commit()

    device = PhoneDevice(
        name=name,
        adb_serial=serial,
        status=device_status,
    )
    db.add(device)
    db.commit()

    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        is_active=assignment_active,
    )
    db.add(assignment)
    db.commit()

    return device, dest


def test_healthy_device_no_status_change_no_alert(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }

    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "available"
    assert client.recover_device.call_count == 0
    assert client.send_notification.call_count == 0

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is None


def test_unhealthy_recover_fixes_it(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.side_effect = [
        {"success": True, "status": "portal_unready", "adb_connected": True, "portal_ready": False},
        {"success": True, "status": "ready", "adb_connected": True, "portal_ready": True},
    ]

    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "available"
    assert client.recover_device.call_count == 1
    assert client.send_notification.call_count == 0

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is not None
    assert marker.state == "resolved"


def test_unhealthy_recover_fails_first_tick_and_second_tick(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    # Both probes return portal_unready
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }
    client.send_notification.return_value = (True, "sent")

    # Tick 1: detects unhealthy, sets maintenance, recovers (fails), sends 1 alert
    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "maintenance"
    assert client.recover_device.call_count == 1
    assert client.send_notification.call_count == 1

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is not None
    assert marker.state == "open"
    assert marker.attempt_count == 1

    alert_notif = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog_alert:{device.id}:{marker.id}")
        .first()
    )
    assert alert_notif is not None
    assert "cannot publish: Mobilerun Portal accessibility unready" in alert_notif.message
    assert "Settings > Accessibility > Mobilerun Portal > On" in alert_notif.message

    # Tick 2 (10 minutes later): still unhealthy, open marker exists
    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "maintenance"
    # recover_device should NOT be retried within 30 minutes
    assert client.recover_device.call_count == 1
    # No new alert should be sent
    assert client.send_notification.call_count == 1


def test_recovers_on_later_tick(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="maintenance")

    # Seed open marker from previous watchdog failure
    marker = OperationalNotification(
        dedupe_key=f"phone_watchdog:{device.id}",
        kind="phone_watchdog",
        message="watchdog_maintenance:open",
        state="open",
        attempt_count=1,
        last_error=datetime.now(timezone.utc).isoformat(),
    )
    db_session.add(marker)
    db_session.commit()

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }
    client.send_notification.return_value = (True, "sent")

    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "available"

    db_session.refresh(marker)
    assert marker.state == "resolved"

    assert client.send_notification.call_count == 1
    sent_msg = client.send_notification.call_args[0][0]
    assert "is healthy again; phone deliveries resumed" in sent_msg


def test_owner_set_maintenance_without_marker_untouched(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="maintenance")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }

    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    # Maintenance set manually by owner must NOT be reset to available
    assert device.status == "maintenance"
    assert client.send_notification.call_count == 0

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is None


def test_active_lease_skipped(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    lease = DeviceLease(
        device_id=device.id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10),
    )
    db_session.add(lease)
    db_session.commit()

    client = MagicMock(spec=PhoneRunnerClient)

    check_phone_devices(client=client, db=db_session)

    assert client.probe_device.call_count == 0
    db_session.refresh(device)
    assert device.status == "available"


def test_runner_unreachable_status_unchanged_no_alert(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": False,
        "status": "runner_unreachable",
        "detail": "Connection refused",
    }

    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "available"
    assert client.recover_device.call_count == 0
    assert client.send_notification.call_count == 0

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is None


def test_hermes_fails_telegram_fallback_called(db_session, caplog):
    from cryptography.fernet import Fernet
    from app.config import get_settings

    fernet_key = Fernet.generate_key().decode()
    with patch.object(get_settings(), "fernet_key", fernet_key):
        device, _ = _seed_device_and_dest(db_session, device_status="available")

        bot_token = "123456:SECRET_BOT_TOKEN"
        settings_row = Settings(
            id=1,
            telegram_bot_token_encrypted=encrypt(bot_token),
            telegram_chat_id="987654321",
        )
        db_session.add(settings_row)
        db_session.commit()

        client = MagicMock(spec=PhoneRunnerClient)
        client.probe_device.return_value = {
            "success": True,
            "status": "portal_unready",
            "adb_connected": True,
            "portal_ready": False,
        }
        # Hermes fails
        client.send_notification.return_value = (False, "hermes_timeout")

        with patch("httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            with caplog.at_level(logging.INFO):
                check_phone_devices(client=client, db=db_session)

            mock_post.assert_called_once()
            url = mock_post.call_args[0][0]
            json_body = mock_post.call_args[1]["json"]
            assert f"/bot{bot_token}/sendMessage" in url
            assert json_body["chat_id"] == "987654321"
            assert "cannot publish: Mobilerun Portal accessibility unready" in json_body["text"]

            # Ensure raw token was NEVER logged
            for record in caplog.records:
                assert bot_token not in record.message


def test_destination_inactive_or_repliz_only_not_probed(db_session):
    client = MagicMock(spec=PhoneRunnerClient)

    # Inactive destination
    dev1, _ = _seed_device_and_dest(db_session, dest_active=False, serial="127.0.0.1:5551", name="Dev1")
    # Repliz-only destination
    dev2, _ = _seed_device_and_dest(db_session, dest_mode="repliz", serial="127.0.0.1:5552", name="Dev2")
    # Inactive assignment
    dev3, _ = _seed_device_and_dest(db_session, assignment_active=False, serial="127.0.0.1:5553", name="Dev3")

    check_phone_devices(client=client, db=db_session)

    assert client.probe_device.call_count == 0


def test_phone_runner_client_probe_device():
    # 1. Missing configuration
    c1 = PhoneRunnerClient(base_url="", token="")
    dev = MagicMock(adb_serial="127.0.0.1:5555")
    res1 = c1.probe_device(device=dev)
    assert res1["success"] is False
    assert res1["status"] == "missing_configuration"

    # 2. Invalid serial
    c2 = PhoneRunnerClient(base_url="http://127.0.0.1:8765", token="secret_token")
    bad_dev = MagicMock(adb_serial="")
    res2 = c2.probe_device(device=bad_dev)
    assert res2["success"] is False
    assert res2["status"] == "runner_unreachable"

    # 3. HTTP 200 response with probe dictionary
    with patch("httpx.Client.post") as mock_post:
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "ready",
            "adb_connected": True,
            "portal_ready": True,
            "screen": "awake",
        }
        mock_post.return_value = mock_resp

        res3 = c2.probe_device(device=dev)
        assert res3["success"] is True
        assert res3["status"] == "ready"
        assert res3["adb_connected"] is True
        assert res3["portal_ready"] is True

    # 4. HTTP network error / non-200
    with patch("httpx.Client.post") as mock_post:
        mock_post.side_effect = Exception("ConnectTimeout")
        res4 = c2.probe_device(device=dev)
        assert res4["success"] is False
        assert res4["status"] == "runner_unreachable"


def test_retry_after_30_minutes_unhealthy(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }
    client.send_notification.return_value = (True, "sent")

    # Tick 1: unhealthy, recover #1
    check_phone_devices(client=client, db=db_session)
    assert client.recover_device.call_count == 1

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is not None
    assert marker.attempt_count == 1

    # Set marker.last_error to 35 min ago
    old_time = (datetime.now(timezone.utc) - timedelta(minutes=35)).isoformat()
    marker.last_error = old_time
    db_session.commit()

    # Tick 2: unhealthy -> recover #2, attempt_count 2
    check_phone_devices(client=client, db=db_session)
    assert client.recover_device.call_count == 2
    db_session.refresh(marker)
    assert marker.attempt_count == 2


def test_no_retry_at_10_minutes_wait_retry_logged(db_session, caplog):
    device, _ = _seed_device_and_dest(db_session, device_status="maintenance")
    marker = OperationalNotification(
        dedupe_key=f"phone_watchdog:{device.id}",
        kind="phone_watchdog",
        message="watchdog_maintenance:open",
        state="open",
        attempt_count=1,
        last_error=(datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat(),
    )
    db_session.add(marker)
    db_session.commit()

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }

    with caplog.at_level(logging.INFO):
        check_phone_devices(client=client, db=db_session)

    assert client.recover_device.call_count == 0
    assert any("action=wait_retry" in rec.message for rec in caplog.records)


def test_future_last_error_treated_as_none_retry_happens(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="maintenance")
    marker = OperationalNotification(
        dedupe_key=f"phone_watchdog:{device.id}",
        kind="phone_watchdog",
        message="watchdog_maintenance:open",
        state="open",
        attempt_count=1,
        last_error=(datetime.now(timezone.utc) + timedelta(hours=2)).isoformat(),
    )
    db_session.add(marker)
    db_session.commit()

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }

    check_phone_devices(client=client, db=db_session)

    assert client.recover_device.call_count == 1
    db_session.refresh(marker)
    assert marker.attempt_count == 2


def test_unparsable_last_error_retry_happens(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="maintenance")
    marker = OperationalNotification(
        dedupe_key=f"phone_watchdog:{device.id}",
        kind="phone_watchdog",
        message="watchdog_maintenance:open",
        state="open",
        attempt_count=1,
        last_error="boom",
    )
    db_session.add(marker)
    db_session.commit()

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }

    check_phone_devices(client=client, db=db_session)

    assert client.recover_device.call_count == 1
    db_session.refresh(marker)
    assert marker.attempt_count == 2


def test_naive_last_error_string_retry_happens(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="maintenance")
    naive_str = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=40)).isoformat()
    marker = OperationalNotification(
        dedupe_key=f"phone_watchdog:{device.id}",
        kind="phone_watchdog",
        message="watchdog_maintenance:open",
        state="open",
        attempt_count=1,
        last_error=naive_str,
    )
    db_session.add(marker)
    db_session.commit()

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }

    check_phone_devices(client=client, db=db_session)

    assert client.recover_device.call_count == 1
    db_session.refresh(marker)
    assert marker.attempt_count == 2


def test_last_seen_at_updated_on_adb_connected_not_on_unreachable(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")
    assert device.last_seen_at is None

    client = MagicMock(spec=PhoneRunnerClient)

    # 1. Healthy probe (adb_connected=True) -> last_seen_at updated
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }
    check_phone_devices(client=client, db=db_session)
    db_session.refresh(device)
    assert device.last_seen_at is not None

    # 2. Unhealthy probe with adb_connected=True -> last_seen_at updated
    device.last_seen_at = datetime(2025, 1, 1, 0, 0, 0)
    db_session.commit()
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }
    check_phone_devices(client=client, db=db_session)
    db_session.refresh(device)
    assert device.last_seen_at > datetime(2025, 1, 1, 0, 0, 0)

    # 3. runner_unreachable -> last_seen_at NOT changed
    pinned_time = datetime(2024, 6, 1, 12, 0, 0)
    device.last_seen_at = pinned_time
    db_session.commit()
    client.probe_device.return_value = {
        "success": False,
        "status": "runner_unreachable",
        "detail": "timeout",
    }
    check_phone_devices(client=client, db=db_session)
    db_session.refresh(device)
    assert device.last_seen_at == pinned_time


def test_probe_device_called_with_timeout_45(db_session):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }

    check_phone_devices(client=client, db=db_session)

    assert client.probe_device.call_count >= 1
    for call in client.probe_device.call_args_list:
        assert call.kwargs.get("timeout") == 45.0


def test_decision_log_line_per_tick_contains_action(db_session, caplog):
    device, _ = _seed_device_and_dest(db_session, device_status="available")

    client = MagicMock(spec=PhoneRunnerClient)
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }

    # 1. Healthy tick -> action=healthy_noop
    with caplog.at_level(logging.INFO):
        check_phone_devices(client=client, db=db_session)

    watchdog_logs = [r.message for r in caplog.records if r.message.startswith("Watchdog: device=")]
    assert len(watchdog_logs) == 1
    assert "action=healthy_noop" in watchdog_logs[0]
    expected_pattern = f"Watchdog: device={device.id} status=available probe=ready adb=True portal=True marker=None last_attempt=None action=healthy_noop"
    assert watchdog_logs[0] == expected_pattern

    # 2. Skip busy tick -> action=skip_busy, probe fields None
    caplog.clear()
    lease = DeviceLease(
        device_id=device.id,
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=10),
    )
    db_session.add(lease)
    db_session.commit()

    with caplog.at_level(logging.INFO):
        check_phone_devices(client=client, db=db_session)

    watchdog_logs = [r.message for r in caplog.records if r.message.startswith("Watchdog: device=")]
    assert len(watchdog_logs) == 1
    assert "action=skip_busy" in watchdog_logs[0]
    assert f"Watchdog: device={device.id} status=available probe=None adb=None portal=None marker=None last_attempt=None action=skip_busy" == watchdog_logs[0]


def test_last_attempt_at_helper_unit():
    from app.tasks.phone_watchdog import _last_attempt_at

    assert _last_attempt_at(None) is None

    m = MagicMock()
    m.last_error = None
    assert _last_attempt_at(m) is None

    m.last_error = ""
    assert _last_attempt_at(m) is None

    m.last_error = "boom"
    assert _last_attempt_at(m) is None

    # Future > 5 min
    m.last_error = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    assert _last_attempt_at(m) is None

    # Future <= 5 min (returns datetime)
    m.last_error = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    assert _last_attempt_at(m) is not None

    # Aware UTC
    m.last_error = "2026-10-19T01:05:00+00:00"
    res = _last_attempt_at(m)
    assert res == datetime(2026, 10, 19, 1, 5, 0)
    assert res.tzinfo is None

    # Aware non-UTC (+07:00)
    m.last_error = "2026-10-19T08:05:00+07:00"
    res = _last_attempt_at(m)
    assert res == datetime(2026, 10, 19, 1, 5, 0)

    # Naive
    m.last_error = "2026-10-19T01:05:00"
    res = _last_attempt_at(m)
    assert res == datetime(2026, 10, 19, 1, 5, 0)
