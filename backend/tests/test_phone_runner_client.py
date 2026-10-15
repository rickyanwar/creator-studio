from unittest import mock
import httpx
import pytest

from app.models.social_publishing import (
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.services.phone_runner_client import PhonePublishResult, PhoneRunnerClient

TEST_BASE_URL = "http://127.0.0.1:8765"
TEST_TOKEN = "secret-phone-token-xyz"
TEST_SERIAL = "100.64.96.50:5555"


@pytest.fixture
def dummy_models():
    device = PhoneDevice(
        id=1,
        name="Realme Test",
        adb_serial=TEST_SERIAL,
        app_card_version="1.0.0",
        screen_width=1080,
        screen_height=2408,
    )
    main_profile = SocialDestination(
        id=10,
        kind="facebook_profile",
        name="Main Profile User",
        facebook_actor_id="1000111222",
    )
    destination = SocialDestination(
        id=20,
        kind="facebook_page",
        name="Tech Page",
        facebook_actor_id="2000111222",
        parent_profile_id=10,
    )
    delivery = PublishDelivery(
        id=100,
        publish_job_id=5,
        destination_id=20,
        caption_snapshot="Exciting news about our launch!",
        media_snapshot=["https://cdn.example.com/img1.jpg"],
        media_kind="image",
    )
    return device, delivery, main_profile, destination


def test_client_init_from_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_BASE_URL", "http://172.17.0.1:8765/")
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", "env-token-123")

    client = PhoneRunnerClient()
    assert client.base_url == "http://172.17.0.1:8765"
    assert client.token == "env-token-123"


def test_publish_delivery_success_200(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    fake_response = {
        "status": "published",
        "facebook_post_id": "9876543210",
        "permalink": "https://www.facebook.com/2000111222/posts/9876543210",
        "actor_id": "2000111222",
        "publish_may_have_started": True,
        "error_code": None,
        "evidence": {"caption_length": 31},
    }

    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert isinstance(result, PhonePublishResult)
    assert result.status == "published"
    assert result.facebook_post_id == "9876543210"
    assert result.permalink == "https://www.facebook.com/2000111222/posts/9876543210"
    assert result.publish_may_have_started is True
    assert result.actor_id == "2000111222"

    # Check request headers & payload structure
    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == f"{TEST_BASE_URL}/publish"
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
    payload = call_kwargs["json"]
    assert payload["serial"] == TEST_SERIAL
    assert payload["main_profile"]["id"] == "1000111222"
    assert payload["main_profile"]["record_id"] == "10"
    assert payload["destination"]["id"] == "2000111222"
    assert payload["destination"]["record_id"] == "20"
    assert payload["destination"]["parent_profile_id"] == "10"
    assert payload["caption"] == "Exciting news about our launch!"
    assert payload["media_urls"] == ["https://cdn.example.com/img1.jpg"]


def test_pre_request_connect_error_maps_to_waiting_for_device(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "waiting_for_device"
    assert result.publish_may_have_started is False
    assert result.error_code == "connect_failed"
    assert "ConnectError" in str(result.evidence)


def test_pre_request_connect_timeout_maps_to_waiting_for_device(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectTimeout("Connect timed out")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "waiting_for_device"
    assert result.publish_may_have_started is False
    assert result.error_code == "connect_failed"


def test_post_connect_read_timeout_maps_to_uncertain(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    with mock.patch("httpx.Client.post", side_effect=httpx.ReadTimeout("Server timed out reading response")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "uncertain"
    assert result.publish_may_have_started is True
    assert result.error_code == "timeout_uncertain"
    assert "ReadTimeout" in str(result.evidence)


def test_post_connect_remote_protocol_error_maps_to_uncertain(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    with mock.patch("httpx.Client.post", side_effect=httpx.RemoteProtocolError("Connection dropped by peer")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "uncertain"
    assert result.publish_may_have_started is True
    assert result.error_code == "timeout_uncertain"


def test_server_5xx_maps_to_uncertain(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    mock_resp = httpx.Response(502, text="Bad Gateway")
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "uncertain"
    assert result.publish_may_have_started is True
    assert result.error_code == "http_502"


def test_client_4xx_maps_to_failed(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    mock_resp = httpx.Response(401, text="Unauthorized")
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "failed"
    assert result.publish_may_have_started is False
    assert result.error_code == "http_401"


def test_no_automatic_retry_on_network_error(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectTimeout("Connect timed out")) as mock_post:
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    # Must be called exactly once; no auto retries
    assert mock_post.call_count == 1


def test_sensitive_data_not_leaked_in_evidence_on_error(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    with mock.patch("httpx.Client.post", side_effect=Exception(f"Error containing token {TEST_TOKEN}")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    evidence_str = str(result.evidence)
    assert TEST_TOKEN not in evidence_str
    assert "token" not in evidence_str
    assert result.error_code == "client_error_Exception"


# ── Tests for PhoneRunnerClient.recover_device ────────────────────────────────


def test_recover_device_success_200(dummy_models):
    device, delivery, _, _ = dummy_models

    fake_response = {
        "status": "resolved",
        "state": "resolved",
        "next_state": "ready",
        "serial": TEST_SERIAL,
        "recovery_class": "routine",
        "reason_code": "routine_check",
        "check_count": 1,
        "hermes_attempt_count": 0,
        "checks": [{"check": "connectivity", "status": "ok"}],
        "owner_notified_at": None,
        "auto_approve_after": None,
        "owner_decision": None,
        "notification_sent": False,
        "error": None,
    }

    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.recover_device(
            device=device,
            delivery=delivery,
            recovery_class="routine",
            reason="routine_check",
        )

    assert result.status == "resolved"
    assert result.state == "resolved"
    assert result.next_state == "ready"
    assert result.serial == TEST_SERIAL
    assert result.check_count == 1
    assert result.hermes_attempt_count == 0
    assert result.error == ""

    mock_post.assert_called_once()
    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == f"{TEST_BASE_URL}/recover"
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
    payload = call_kwargs["json"]
    assert payload["serial"] == TEST_SERIAL
    assert payload["recovery_class"] == "routine"
    assert payload["reason"] == "routine_check"


def test_recover_device_rejects_invalid_serial(dummy_models):
    device, delivery, _, _ = dummy_models
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    bad_serials = [
        "",
        "   ",
        f"{TEST_SERIAL}; reboot",
        "$(whoami)",
        "`id`",
    ]
    for bad in bad_serials:
        device.adb_serial = bad
        result = client.recover_device(device=device, delivery=delivery)
        assert result.status == "failed"
        assert result.state == "needs_human"
        assert result.error == "invalid_serial"


def test_recover_device_rejects_invalid_recovery_class_or_reason(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    # Invalid class
    res_class = client.recover_device(
        device=device,
        delivery=delivery,
        recovery_class="arbitrary_class",
    )
    assert res_class.status == "failed"
    assert res_class.error == "invalid_recovery_class"

    # Invalid reason
    res_reason = client.recover_device(
        device=device,
        delivery=delivery,
        reason="bad; reason with spaces",
    )
    assert res_reason.status == "failed"
    assert res_reason.error == "invalid_reason"


def test_recover_device_connect_error_maps_to_failed(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL

    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectError("Connection refused")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.recover_device(device=device, delivery=delivery)

    assert result.status == "failed"
    assert result.state == "needs_human"
    assert result.error == "connect_failed"


def test_recover_device_timeout_maps_to_failed(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL

    with mock.patch("httpx.Client.post", side_effect=httpx.ReadTimeout("Timed out")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.recover_device(device=device, delivery=delivery)

    assert result.status == "failed"
    assert result.state == "needs_human"
    assert result.error == "timeout"


def test_recover_device_5xx_maps_to_failed(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL

    mock_resp = httpx.Response(503, text="Service Unavailable")
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.recover_device(device=device, delivery=delivery)

    assert result.status == "failed"
    assert result.state == "needs_human"
    assert result.error == "http_503"


def test_recover_device_sanitizes_secret_in_error(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL

    secret = "SECRET_TOKEN_XYZ_999"
    fake_response = {
        "status": "failed",
        "state": "needs_human",
        "next_state": "needs_human",
        "error": f"Failed with Bearer {secret} and password={secret}",
    }
    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.recover_device(device=device, delivery=delivery)

    assert secret not in result.error
    assert "[REDACTED]" in result.error


def test_recover_device_payload_includes_approval_and_decision(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL

    fake_response = {
        "status": "awaiting_approval",
        "state": "awaiting_approval",
        "next_state": "awaiting_approval",
        "serial": TEST_SERIAL,
        "recovery_class": "larger_repair",
        "reason_code": "system_unresponsive",
        "check_count": 1,
        "hermes_attempt_count": 0,
        "owner_notified_at": "2026-10-15T12:00:00",
        "auto_approve_after": "2026-10-15T12:15:00",
        "owner_decision": "approved",
        "notification_sent": True,
    }

    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.recover_device(
            device=device,
            delivery=delivery,
            recovery_class="larger_repair",
            reason="system_unresponsive",
            owner_decision="approved",
            auto_approve_after="2026-10-15T12:15:00",
            owner_notified_at="2026-10-15T12:00:00",
        )

    assert result.status == "awaiting_approval"
    assert result.notification_sent is True
    assert result.owner_decision == "approved"

    _, call_kwargs = mock_post.call_args
    payload = call_kwargs["json"]
    assert payload["owner_decision"] == "approved"
    assert payload["auto_approve_after"] == "2026-10-15T12:15:00"
    assert payload["owner_notified_at"] == "2026-10-15T12:00:00"


# ── Dry-Run & Notification Contract Tests ────────────────────────────────────


def test_dry_run_success_payload_and_headers(dummy_models):
    device, _, main_profile, destination = dummy_models
    fake_response = {
        "success": True,
        "status": "ready",
        "evidence": {"verified_actor": "Main Profile User", "verified_actor_id": "1000111222"},
    }
    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        res = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
            target_page={"id": "2000111222", "name": destination.name},
        )

    assert res["success"] is True
    assert res["status"] == "ready"
    assert res["evidence"]["verified_actor_id"] == "1000111222"

    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == f"{TEST_BASE_URL}/dry-run"
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
    assert call_kwargs["headers"]["Content-Type"] == "application/json"
    payload = call_kwargs["json"]
    assert payload["serial"] == TEST_SERIAL
    assert payload["expected_geometry"] == {"width": 1080, "height": 2408}
    assert payload["app_card_version"] == "1.0.0"


def test_dry_run_bounded_timeout_and_injection(dummy_models):
    device, _, main_profile, destination = dummy_models
    fake_response = {"success": True, "status": "ready"}
    mock_resp = httpx.Response(200, json=fake_response)

    # 1. Default dry-run timeout is 260.0s (bounded for 240s host proof + 20s transport, < 300s lease)
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
    assert client.dry_run_timeout == 260.0
    assert client.timeout == 120.0  # publish timeout untouched

    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )
        assert mock_client_cls.call_count == 1
        _, client_kwargs = mock_client_cls.call_args
        t_cfg = client_kwargs["timeout"]
        assert t_cfg.read == 260.0
        assert t_cfg.connect == 10.0

    # 2. Injected call-level timeout
    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
            timeout=250.0,
        )
        _, client_kwargs = mock_client_cls.call_args
        t_cfg = client_kwargs["timeout"]
        assert t_cfg.read == 250.0

    # 3. Custom client-level dry_run_timeout
    custom_client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN, dry_run_timeout=255.0)
    assert custom_client.dry_run_timeout == 255.0
    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        custom_client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )
        _, client_kwargs = mock_client_cls.call_args
        t_cfg = client_kwargs["timeout"]
        assert t_cfg.read == 255.0


def test_dry_run_missing_serial(dummy_models):
    device, _, main_profile, _ = dummy_models
    device.adb_serial = ""

    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
    res = client.dry_run(
        device=device,
        main_profile={"id": "1000111222", "name": main_profile.name},
    )
    assert res["success"] is False
    assert res["status"] == "missing_serial"


def test_dry_run_absent_configuration(dummy_models):
    device, _, main_profile, _ = dummy_models
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token="")
    res = client.dry_run(
        device=device,
        main_profile={"id": "1000111222", "name": main_profile.name},
    )
    assert res["success"] is False
    assert res["status"] == "missing_configuration"


@pytest.mark.parametrize("status_code", [401, 409, 500, 503])
def test_dry_run_http_error_codes(dummy_models, status_code):
    device, _, main_profile, _ = dummy_models
    secret = "TOP_SECRET_AUTH_KEY"
    mock_resp = httpx.Response(status_code, text=f"Error with {secret}")
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        res = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )

    assert res["success"] is False
    assert res["status"] == f"http_{status_code}"
    # Raw body or secret must NOT be leaked
    assert secret not in str(res)


def test_dry_run_connect_timeout(dummy_models):
    device, _, main_profile, _ = dummy_models
    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectTimeout("Connect failed")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        res = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )

    assert res["success"] is False
    assert res["status"] == "connect_timeout"


def test_dry_run_read_timeout(dummy_models):
    device, _, main_profile, _ = dummy_models
    with mock.patch("httpx.Client.post", side_effect=httpx.ReadTimeout("Read timed out")):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        res = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )

    assert res["success"] is False
    assert res["status"] == "read_timeout"


def test_dry_run_malformed_json_and_non_dict(dummy_models):
    device, _, main_profile, _ = dummy_models

    mock_resp1 = httpx.Response(200, text="Not a JSON")
    with mock.patch("httpx.Client.post", return_value=mock_resp1):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        res1 = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )
    assert res1["success"] is False
    assert res1["status"] == "malformed_response"

    mock_resp2 = httpx.Response(200, json=["unexpected", "array"])
    with mock.patch("httpx.Client.post", return_value=mock_resp2):
        res2 = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )
    assert res2["success"] is False
    assert res2["status"] == "malformed_response"


@pytest.mark.parametrize("invalid_flag", ["false", "true", "1", 1, None])
def test_dry_run_invalid_boolean_success_flag(dummy_models, invalid_flag):
    device, _, main_profile, _ = dummy_models
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    mock_resp = httpx.Response(200, json={"success": invalid_flag, "status": "ready"})
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        res = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )
    assert res["success"] is False
    assert res["status"] == "malformed_response"


def test_dry_run_missing_success_flag(dummy_models):
    device, _, main_profile, _ = dummy_models
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    mock_resp = httpx.Response(200, json={"status": "ready"})
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        res = client.dry_run(
            device=device,
            main_profile={"id": "1000111222", "name": main_profile.name},
        )
    assert res["success"] is False
    assert res["status"] == "malformed_response"


def test_notify_owner_success_payload_and_headers():
    fake_response = {"sent": True, "detail": "Telegram notification delivered"}
    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp) as mock_post:
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        sent, detail = client.notify_owner("Device need human intervention")

    assert sent is True
    assert "delivered" in detail

    call_args, call_kwargs = mock_post.call_args
    assert call_args[0] == f"{TEST_BASE_URL}/notify"
    assert call_kwargs["headers"]["Authorization"] == f"Bearer {TEST_TOKEN}"
    payload = call_kwargs["json"]
    assert payload["message"] == "Device need human intervention"
    assert payload["channel"] == "telegram"


def test_notify_owner_absent_configuration():
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token="")
    sent, detail = client.notify_owner("Hello")
    assert sent is False
    assert detail == "missing_configuration"


@pytest.mark.parametrize("status_code", [401, 409, 500, 503])
def test_notify_owner_http_errors(status_code):
    secret = "SUPER_SECRET_BEARER"
    mock_resp = httpx.Response(status_code, text=f"Error: {secret}")
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        sent, detail = client.notify_owner("Hello")

    assert sent is False
    assert detail == f"http_{status_code}"
    assert secret not in detail


def test_notify_owner_timeouts():
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    with mock.patch("httpx.Client.post", side_effect=httpx.ConnectTimeout("Connect fail")):
        sent, detail = client.notify_owner("Hello")
        assert sent is False
        assert "ConnectTimeout" in detail

    with mock.patch("httpx.Client.post", side_effect=httpx.ReadTimeout("Read fail")):
        sent, detail = client.notify_owner("Hello")
        assert sent is False
        assert "ReadTimeout" in detail


def test_notify_owner_malformed_json_and_non_dict():
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    mock_resp1 = httpx.Response(200, text="Not JSON")
    with mock.patch("httpx.Client.post", return_value=mock_resp1):
        sent, detail = client.notify_owner("Hello")
        assert sent is False
        assert detail == "malformed_response"

    mock_resp2 = httpx.Response(200, json=["unexpected", "list"])
    with mock.patch("httpx.Client.post", return_value=mock_resp2):
        sent, detail = client.notify_owner("Hello")
        assert sent is False
        assert detail == "malformed_response"


@pytest.mark.parametrize("invalid_flag", ["false", "true", "1", 1, None])
def test_notify_owner_invalid_boolean_sent_flag(invalid_flag):
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    mock_resp = httpx.Response(200, json={"sent": invalid_flag, "detail": "test"})
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        sent, detail = client.notify_owner("Hello")
    assert sent is False
    assert detail == "malformed_response"


def test_notify_owner_missing_sent_flag():
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)

    mock_resp = httpx.Response(200, json={"detail": "test"})
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        sent, detail = client.notify_owner("Hello")
    assert sent is False
    assert detail == "malformed_response"


@pytest.mark.parametrize(
    "payload_actor,expected_actor",
    [
        (None, None),
        ("", ""),
        ("   ", "   "),
        ("wrong_actor_777", "wrong_actor_777"),
        ("2000111222", "2000111222"),
    ],
)
def test_publish_delivery_http200_actor_id_never_falls_back_to_destination(
    dummy_models, payload_actor, expected_actor
):
    device, delivery, main_profile, destination = dummy_models

    fake_response = {
        "status": "published",
        "facebook_post_id": "9876543210",
        "permalink": "https://www.facebook.com/2000111222/posts/9876543210",
        "publish_may_have_started": True,
        "error_code": None,
        "evidence": {"caption_length": 31},
    }
    if payload_actor is not None:
        fake_response["actor_id"] = payload_actor

    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "published"
    assert result.facebook_post_id == "9876543210"
    assert result.actor_id == expected_actor


def test_publish_delivery_http200_explicit_null_actor_id_no_fallback(dummy_models):
    device, delivery, main_profile, destination = dummy_models

    fake_response = {
        "status": "published",
        "facebook_post_id": "9876543210",
        "permalink": "https://www.facebook.com/2000111222/posts/9876543210",
        "actor_id": None,
        "publish_may_have_started": True,
        "error_code": None,
    }

    mock_resp = httpx.Response(200, json=fake_response)
    with mock.patch("httpx.Client.post", return_value=mock_resp):
        client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

    assert result.status == "published"
    assert result.actor_id is None


def test_phone_runner_client_timeout_budgets_and_constructor_defaults():
    client = PhoneRunnerClient()
    # ordinary / probe / recover default unchanged at 120.0s
    assert client.timeout == 120.0
    # connect timeout unchanged at 10.0s
    assert client.connect_timeout == 10.0
    # dry run timeout unchanged at 260.0s
    assert client.dry_run_timeout == 260.0
    # publish delivery timeout is 280.0s (240s collector + 40s prep/cleanup < 300s lease)
    assert client.publish_timeout == 280.0


def test_phone_runner_client_publish_timeout_custom_override():
    client = PhoneRunnerClient(
        base_url=TEST_BASE_URL,
        token=TEST_TOKEN,
        timeout=110.0,
        connect_timeout=8.0,
        dry_run_timeout=250.0,
        publish_timeout=275.0,
    )
    assert client.timeout == 110.0
    assert client.connect_timeout == 8.0
    assert client.dry_run_timeout == 250.0
    assert client.publish_timeout == 275.0


def test_publish_delivery_uses_publish_timeout_budget(dummy_models):
    device, delivery, main_profile, destination = dummy_models
    fake_response = {
        "status": "published",
        "facebook_post_id": "9876543210",
        "actor_id": "2000111222",
    }
    mock_resp = httpx.Response(200, json=fake_response)

    # 1. Default client: timeout passed to httpx.Client must have read=280.0, connect=10.0
    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN)
    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

        assert mock_client_cls.call_count == 1
        _, client_kwargs = mock_client_cls.call_args
        t_cfg = client_kwargs["timeout"]
        assert t_cfg.read == 280.0
        assert t_cfg.connect == 10.0

    # 2. Custom override: publish_timeout=275.0
    custom_client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN, publish_timeout=275.0)
    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        custom_client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

        _, client_kwargs = mock_client_cls.call_args
        t_cfg = client_kwargs["timeout"]
        assert t_cfg.read == 275.0


def test_recover_device_preserves_ordinary_timeout(dummy_models):
    device, delivery, _, _ = dummy_models
    device.adb_serial = TEST_SERIAL
    fake_response = {"status": "resolved", "state": "resolved", "next_state": "ready"}
    mock_resp = httpx.Response(200, json=fake_response)

    client = PhoneRunnerClient(base_url=TEST_BASE_URL, token=TEST_TOKEN, publish_timeout=280.0)
    with mock.patch("httpx.Client") as mock_client_cls:
        mock_instance = mock.MagicMock()
        mock_instance.post.return_value = mock_resp
        mock_client_cls.return_value.__enter__.return_value = mock_instance

        client.recover_device(device=device, delivery=delivery)

        assert mock_client_cls.call_count == 1
        _, client_kwargs = mock_client_cls.call_args
        t_cfg = client_kwargs["timeout"]
        assert t_cfg.read == 120.0
        assert t_cfg.connect == 10.0
