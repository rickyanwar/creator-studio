from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.config import get_max_media_bytes
from phone_runner.mobilerun import MobilerunResult
from phone_runner.publish import execute_publish, pre_share_max_seconds
from phone_runner.tests.test_modern_composer import (
    EMPTY_COMPOSER_XML,
    POST_SETTINGS_XML,
    TYPED_COMPOSER_XML,
    _make_valid_proof,
)

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100009439229483"
TEST_ROOT_NAME = "Agung"
TEST_DEST_ID = "100009439229483"
TEST_DEST_NAME = "Agung"

FAKE_PROBE_READY = {
    "status": "ready",
    "reachable": True,
    "adb_connected": True,
    "portal_ready": True,
    "screen": "awake",
    "resolution": "1080x2408",
    "rotation": 0,
    "facebook_version": "580.0.0.51.74",
    "portal_version": "0.7.25",
    "checked_at": "2026-10-14T12:00:00Z",
}


def test_publish_share_not_tapped_due_to_identity_failure_force_stops_at_end(monkeypatch):
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    # Proof fails
    mock_proof = mock.Mock(
        passed=False,
        error="Target profile not found",
        observed_root_id=TEST_ROOT_ID,
        verified_main_profile_id=TEST_ROOT_ID,
        observed_target_id=None,
        active_composer_actor_id=None,
        serial=TEST_SERIAL,
        method="own_url_chain_v1",
        resolution="1080x2408",
        app_card_version="1.0.0",
        facebook_version="580.0.0.51.74",
        portal_version="0.7.25",
        nonce="test",
    )

    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.verify_mobilerun_service", lambda s: (True, "pong"))
    monkeypatch.setattr("phone_runner.publish.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.publish.run_adb", record_adb)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: AdbResult(0, "<xml/>", ""))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: mock_proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="test caption",
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"

    # Force-stop called TWICE: 1 at start (app_reset), 1 at end (app_closed)
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 2

    # Both app_reset and app_closed stages present
    stages = [s["stage"] for s in res.get("stages", [])]
    assert "app_reset" in stages
    assert "app_closed" in stages


def test_publish_published_with_post_id_force_stops_at_end(monkeypatch):
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.verify_mobilerun_service", lambda s: (True, "pong"))
    monkeypatch.setattr("phone_runner.publish.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, TYPED_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, rng=None: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", record_adb)
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    resolved_post_id = "pfbid02rX8abc"
    resolved_permalink = f"https://www.facebook.com/permalink.php?story_fbid={resolved_post_id}&id={TEST_DEST_ID}"
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: (resolved_post_id, resolved_permalink, "verified via top post"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == resolved_post_id

    # Force-stop called TWICE: 1 at start (app_reset), 1 at end (app_closed)
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 2

    stages = [s["stage"] for s in res.get("stages", [])]
    assert "app_reset" in stages
    assert "app_closed" in stages


def test_publish_uncertain_after_share_does_not_force_stop_at_end(monkeypatch):
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.verify_mobilerun_service", lambda s: (True, "pong"))
    monkeypatch.setattr("phone_runner.publish.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, TYPED_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, rng=None: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", record_adb)
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    # Post link NOT found after SHARE -> uncertain
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: (None, None, "post not found"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
    )

    assert res["status"] == "uncertain"

    # Force-stop called ONLY ONCE: at start (app_reset), NOT at end!
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 1

    stages = [s["stage"] for s in res.get("stages", [])]
    assert "app_reset" in stages
    assert "app_closed" not in stages


def test_publish_rehearsal_closes_facebook_at_end(monkeypatch):
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.verify_mobilerun_service", lambda s: (True, "pong"))
    monkeypatch.setattr("phone_runner.publish.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, TYPED_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, rng=None: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", record_adb)
    monkeypatch.setattr("phone_runner.publish.discard_composer", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
        rehearsal=True,
    )

    assert res["status"] == "rehearsal_ok"

    # Force-stop called TWICE: 1 at start (app_reset), 1 at end (app_closed)
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 2

    stages = [s["stage"] for s in res.get("stages", [])]
    assert "app_reset" in stages
    assert "app_closed" in stages


def test_size_aware_limits_and_pre_share_budget(monkeypatch):
    # Default max media bytes is 300 MiB
    monkeypatch.delenv("PHONE_RUNNER_MAX_MEDIA_BYTES", raising=False)
    assert get_max_media_bytes() == 300 * 1024 * 1024

    # Pre-share max seconds:
    # 0 bytes (text-only) -> 300.0
    assert pre_share_max_seconds(0) == 300.0

    # 10 KB (small image) -> ~300.04
    assert pre_share_max_seconds(10 * 1024) == pytest.approx(300.04, abs=0.1)

    # 10 MB (10 * 1024 * 1024 = 10,485,760 bytes):
    # push timeout = 45 + 10485760 / 262144 = 85.0s
    # extra = 85.0 - 45.0 = 40.0s
    # budget = 300 + 40 = 340.0s
    assert pre_share_max_seconds(10 * 1024 * 1024) == pytest.approx(340.0)

    # 200 MB (large video):
    # push timeout = min(45 + 200*1024/256, 600) = 600.0s
    # extra = 600.0 - 45.0 = 555.0s
    # budget = min(300 + 555, 420) = 420.0s
    assert pre_share_max_seconds(200 * 1024 * 1024) == 420.0
    assert 300.0 < pre_share_max_seconds(200 * 1024 * 1024) <= 420.0
