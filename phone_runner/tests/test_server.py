import json
import os
import threading
from unittest import mock
import urllib.error
import urllib.request

import pytest

from phone_runner.server import create_server

TEST_TOKEN = "secret-test-token-12345"
TEST_SERIAL = "100.64.96.50:5555"


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_BIND", "127.0.0.1")
    monkeypatch.setenv("PHONE_RUNNER_PORT", "0")


@pytest.fixture
def running_server():
    server = create_server("127.0.0.1", 0)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def make_request(
    url: str,
    method: str = "GET",
    headers: dict | None = None,
    data: dict | bytes | str | None = None,
):
    h = headers.copy() if headers else {}
    body = None
    if isinstance(data, dict):
        body = json.dumps(data).encode("utf-8")
        h.setdefault("Content-Type", "application/json")
    elif isinstance(data, (bytes, bytearray)):
        body = bytes(data)
    elif isinstance(data, str):
        body = data.encode("utf-8")

    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        parsed = {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            parsed = {"raw": raw.decode("utf-8", errors="replace")}
        return exc.code, parsed


def test_health_no_auth_required(running_server):
    status, body = make_request(f"{running_server}/health")
    assert status == 200
    assert body["status"] == "ok"
    assert body["service"] == "phone-runner"


def test_probe_missing_auth_rejected(running_server):
    status, body = make_request(
        f"{running_server}/probe",
        method="POST",
        data={"serial": TEST_SERIAL},
    )
    assert status == 401
    assert body["error"] == "unauthorized"


def test_probe_invalid_token_rejected(running_server):
    status, body = make_request(
        f"{running_server}/probe",
        method="POST",
        headers={"Authorization": "Bearer wrong-token"},
        data={"serial": TEST_SERIAL},
    )
    assert status == 401
    assert body["error"] == "unauthorized"


def test_probe_x_token_header_accepted(running_server):
    with mock.patch("phone_runner.server.probe_device") as mock_probe:
        mock_probe.return_value = {"status": "ready", "adb_connected": True}
        status, body = make_request(
            f"{running_server}/probe",
            method="POST",
            headers={"X-Phone-Runner-Token": TEST_TOKEN},
            data={"serial": TEST_SERIAL},
        )
        assert status == 200
        assert body["status"] == "ready"


def test_reject_serial_not_in_allowlist(running_server):
    status, body = make_request(
        f"{running_server}/probe",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"serial": "192.168.1.99:5555"},
    )
    assert status == 403
    assert body["error"] == "serial_not_allowed"


def test_reject_shell_injection_in_serial(running_server):
    injections = [
        f"{TEST_SERIAL}; rm -rf /",
        f"{TEST_SERIAL} && reboot",
        "$(whoami)",
        "`cat /etc/passwd`",
        f"{TEST_SERIAL} | touch /tmp/pwned",
        f"{TEST_SERIAL} > /dev/null",
    ]
    for bad_serial in injections:
        status, body = make_request(
            f"{running_server}/probe",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={"serial": bad_serial},
        )
        assert status == 400
        assert body["error"] == "invalid_serial_format"


def test_reject_missing_serial(running_server):
    status, body = make_request(
        f"{running_server}/probe",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"wake": True},
    )
    assert status == 400
    assert body["error"] == "missing_serial"


def test_reject_malformed_json(running_server):
    status, body = make_request(
        f"{running_server}/probe",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}", "Content-Type": "application/json"},
        data="{bad_json: invalid",
    )
    assert status == 400
    assert body["error"] == "malformed_json"


def test_reject_payload_too_large(running_server):
    oversize = b"x" * (70 * 1024)
    status, body = make_request(
        f"{running_server}/probe",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}", "Content-Type": "application/json"},
        data=oversize,
    )
    assert status in (413, 400)


def test_dry_run_requires_main_profile(running_server):
    status, body = make_request(
        f"{running_server}/dry-run",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"serial": TEST_SERIAL},
    )
    assert status == 400
    assert body["error"] == "missing_main_profile"


def test_dry_run_authenticated_success(running_server):
    with mock.patch("phone_runner.server.execute_dry_run") as mock_dry:
        mock_dry.return_value = {"success": True, "status": "ready", "stages": []}
        status, body = make_request(
            f"{running_server}/dry-run",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={"serial": TEST_SERIAL, "main_profile": {"name": "Test User"}},
        )
        assert status == 200
        assert body["success"] is True
        assert body["status"] == "ready"


def test_publish_missing_auth_rejected(running_server):
    status, body = make_request(
        f"{running_server}/publish",
        method="POST",
        data={"serial": TEST_SERIAL},
    )
    assert status == 401
    assert body["error"] == "unauthorized"


def test_publish_serial_not_allowed(running_server):
    status, body = make_request(
        f"{running_server}/publish",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"serial": "192.168.1.99:5555"},
    )
    assert status == 403
    assert body["error"] == "serial_not_allowed"


def test_publish_missing_destination(running_server):
    status, body = make_request(
        f"{running_server}/publish",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"serial": TEST_SERIAL, "main_profile": {"id": "1001", "name": "Ricky"}},
    )
    assert status == 400
    assert body["error"] == "missing_destination"


def test_publish_authenticated_success(running_server):
    with mock.patch("phone_runner.server.execute_publish") as mock_pub:
        mock_pub.return_value = {
            "status": "published",
            "facebook_post_id": "123456",
            "permalink": "https://fb.com/123456",
            "publish_may_have_started": True,
        }
        status, body = make_request(
            f"{running_server}/publish",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={
                "serial": TEST_SERIAL,
                "main_profile": {"id": "1001", "name": "Ricky"},
                "destination": {"id": "1001", "name": "Ricky", "kind": "facebook_profile"},
                "caption": "Hello world",
            },
        )
        assert status == 200
        assert body["status"] == "published"
        assert body["facebook_post_id"] == "123456"


def test_not_found_routes(running_server):
    status, body = make_request(f"{running_server}/non-existent")
    assert status == 404
    assert body["error"] == "not_found"


def test_serial_lock_prevents_overlapping_mutating_calls(running_server, monkeypatch):
    from phone_runner.server import get_serial_lock

    second_serial = "100.64.96.51:5555"
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", f"{TEST_SERIAL},{second_serial}")

    lock = get_serial_lock(TEST_SERIAL)
    assert lock.acquire(blocking=False)
    try:
        # Mutating call on locked serial must return 409 Conflict
        status, body = make_request(
            f"{running_server}/dry-run",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={"serial": TEST_SERIAL, "main_profile": {"id": "1001", "name": "Ricky"}},
        )
        assert status == 409
        assert body["error"] == "device_busy"

        # Different serial must be allowed concurrently
        with mock.patch("phone_runner.server.execute_dry_run") as mock_dry:
            mock_dry.return_value = {"success": True, "status": "ready"}
            status2, body2 = make_request(
                f"{running_server}/dry-run",
                method="POST",
                headers={"Authorization": f"Bearer {TEST_TOKEN}"},
                data={"serial": second_serial, "main_profile": {"id": "1001", "name": "Ricky"}},
            )
            assert status2 == 200
            assert body2["success"] is True
    finally:
        lock.release()

    # After lock released, original serial can be acquired
    with mock.patch("phone_runner.server.execute_dry_run") as mock_dry:
        mock_dry.return_value = {"success": True, "status": "ready"}
        status3, body3 = make_request(
            f"{running_server}/dry-run",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={"serial": TEST_SERIAL, "main_profile": {"id": "1001", "name": "Ricky"}},
        )
        assert status3 == 200
        assert body3["success"] is True


def test_notify_endpoint_telegram_only_and_no_serial_needed(running_server):
    # Missing auth -> 401
    status, body = make_request(
        f"{running_server}/notify",
        method="POST",
        data={"message": "System alert: battery low"},
    )
    assert status == 401

    # Empty message -> 400
    status, body = make_request(
        f"{running_server}/notify",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"message": ""},
    )
    assert status == 400
    assert body["error"] == "missing_or_invalid_message"

    # Unsupported channel -> 400
    status, body = make_request(
        f"{running_server}/notify",
        method="POST",
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        data={"message": "System alert", "channel": "email"},
    )
    assert status == 400
    assert body["error"] == "unsupported_channel"

    # Success: no serial required, calls send_owner_notification
    with mock.patch("phone_runner.recovery.send_owner_notification", return_value=(True, "sent")) as mock_send:
        status, body = make_request(
            f"{running_server}/notify",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={"message": "Device recovery required for 100.64.96.50:5555"},
        )
        assert status == 200
        assert body["sent"] is True
        assert body["detail"] == "sent"
        mock_send.assert_called_once_with("Device recovery required for 100.64.96.50:5555")
