from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import collect_own_url_proof_chain
from phone_runner.mobilerun import MobilerunResult
from phone_runner.publish import (
    POSTED_VISION_RETRIES,
    POSTED_VISION_RETRY_WAIT_S,
    VISION_INFRA_MARKER,
    execute_publish,
)
from phone_runner.tests.test_identity_proof import (
    ROOT_COMPOSER_XML,
    ROOT_PROFILE_XML,
    ROOT_SETTINGS_XML,
    AdbCommandRecorder,
    make_mock_clipboard,
)
from phone_runner.tests.test_modern_publish import (
    EMPTY_PROOF_COMPOSER_XML,
    FAKE_PROBE_READY,
    MODERN_FEED_DUMP,
    POST_SETTINGS_SCREEN_XML,
    SHARE_INTENT_COMPOSER_XML,
    TEST_DEST_ID,
    TEST_DEST_NAME,
    TEST_ROOT_ID,
    TEST_ROOT_NAME,
    TEST_SERIAL,
    TYPED_SHARE_COMPOSER_XML,
    _make_valid_modern_proof,
)


@pytest.fixture(autouse=True)
def guard_no_unmocked_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_subprocess(*args, **kwargs):
        raise AssertionError("Unmocked subprocess execution")

    def forbid_http(*args, **kwargs):
        raise AssertionError("Unmocked HTTP execution")

    monkeypatch.setattr("subprocess.run", forbid_subprocess)
    monkeypatch.setattr("subprocess.Popen", forbid_subprocess)
    monkeypatch.setattr("urllib.request.urlopen", forbid_http)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbid_http)


@pytest.fixture(autouse=True)
def _common_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "5242880")
    monkeypatch.setattr(
        "phone_runner.publish.ping_portal",
        lambda s: AdbResult(0, "Row: 0 result=pong\n", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.wake_and_unlock",
        lambda s: AdbResult(0, "OK", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.is_device_secure_locked",
        lambda s: False,
    )


@pytest.fixture
def mock_device_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_power_state",
        lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""),
    )
    default_window_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}"
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_window_state",
        lambda s: AdbResult(0, default_window_dump, ""),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_activity_state",
        lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""),
    )
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.put_to_sleep", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.live_dry_run.query_power_state",
        lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""),
    )
    monkeypatch.setattr("phone_runner.live_dry_run.put_to_sleep", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", AdbCommandRecorder())

    default_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-15T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(default_probe))


def _setup_publish_mocks(monkeypatch: pytest.MonkeyPatch, posted_gate_side_effect):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    sleep_calls: list[float] = []
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps: list[tuple[int, int, int, int]] = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [
        {"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}
    ]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr(
        "phone_runner.publish.get_media_content_uri",
        lambda s, p: "content://media/external/images/media/101",
    )
    monkeypatch.setattr(
        "phone_runner.publish.open_share_intent_composer",
        lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML),
    )
    monkeypatch.setattr(
        "phone_runner.publish.type_text",
        lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""),
    )

    gates_called: list[str] = []

    def mock_verify_gate(serial, gate, **kw):
        gates_called.append(gate)
        if gate == "posted":
            return posted_gate_side_effect()
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_12345", "https://facebook.com/posts/post_12345", "resolved"),
    )

    return sleep_calls, taps, gates_called


def test_posted_gate_infra_failure_retries_and_skips(monkeypatch: pytest.MonkeyPatch):
    posted_attempts = 0

    def posted_gate():
        nonlocal posted_attempts
        posted_attempts += 1
        return False, {
            "gate": "posted",
            "model": "",
            "ok": False,
            "detail": f"{VISION_INFRA_MARKER}; last detail: model gemini error: HTTPError attempt {posted_attempts}",
            "answer": None,
        }

    sleep_calls, taps, gates_called = _setup_publish_mocks(monkeypatch, posted_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_12345"
    assert res["permalink"] == "https://facebook.com/posts/post_12345"
    assert res["error_code"] is None
    assert res["evidence"]["vision_posted_skipped"] is True
    assert any(
        s["stage"] == "vision_gate_posted"
        and s["passed"] is True
        and "skipped: vision service unavailable" in s["detail"]
        for s in res["stages"]
    )
    assert posted_attempts == 1 + POSTED_VISION_RETRIES
    assert len([g for g in res["evidence"]["vision_gates"] if g["gate"] == "posted"]) == 3


def test_posted_gate_infra_failure_retry_success(monkeypatch: pytest.MonkeyPatch):
    posted_attempts = 0

    def posted_gate():
        nonlocal posted_attempts
        posted_attempts += 1
        if posted_attempts == 1:
            return False, {
                "gate": "posted",
                "model": "",
                "ok": False,
                "detail": f"{VISION_INFRA_MARKER}; last detail: model gemini error: HTTPError",
                "answer": None,
            }
        return True, {
            "gate": "posted",
            "model": "gemini-flash",
            "ok": True,
            "detail": "posted verified",
            "answer": {},
        }

    sleep_calls, taps, gates_called = _setup_publish_mocks(monkeypatch, posted_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_12345"
    assert res["permalink"] == "https://facebook.com/posts/post_12345"
    assert res["error_code"] is None
    assert "vision_posted_skipped" not in res["evidence"]
    assert any(
        s["stage"] == "vision_gate_posted"
        and s["passed"] is True
        and s["detail"] == "posted verified"
        for s in res["stages"]
    )
    assert posted_attempts == 2
    assert len([g for g in res["evidence"]["vision_gates"] if g["gate"] == "posted"]) == 2


def test_posted_gate_real_mismatch_fails_uncertain(monkeypatch: pytest.MonkeyPatch):
    posted_attempts = 0

    def posted_gate():
        nonlocal posted_attempts
        posted_attempts += 1
        return False, {
            "gate": "posted",
            "model": "gemini-flash",
            "ok": False,
            "detail": "photo not visible on profile",
            "answer": {},
        }

    sleep_calls, taps, gates_called = _setup_publish_mocks(monkeypatch, posted_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "uncertain"
    assert res["error_code"] == "post_vision_mismatch"
    assert res["facebook_post_id"] is None
    assert res["evidence"]["resolved_post_id"] == "post_12345"
    assert "vision_posted_skipped" not in res["evidence"]
    assert posted_attempts == 1
    assert len([g for g in res["evidence"]["vision_gates"] if g["gate"] == "posted"]) == 1


def test_posted_gate_sleep_called_between_retries(monkeypatch: pytest.MonkeyPatch):
    def posted_gate():
        return False, {
            "gate": "posted",
            "model": "",
            "ok": False,
            "detail": f"{VISION_INFRA_MARKER}; last detail: timeout",
            "answer": None,
        }

    sleep_calls, taps, gates_called = _setup_publish_mocks(monkeypatch, posted_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    retry_sleeps = [s for s in sleep_calls if s == POSTED_VISION_RETRY_WAIT_S]
    assert len(retry_sleeps) == POSTED_VISION_RETRIES
    assert retry_sleeps == [20, 20]


def test_identity_copy_root_link_retry_success(mock_device_env, monkeypatch: pytest.MonkeyPatch):
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        ROOT_COMPOSER_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    copy_calls = 0

    def mock_tap_copy(serial, bounds, sentinel, **kw):
        nonlocal copy_calls
        copy_calls += 1
        if copy_calls == 1:
            return None, "timeout"
        return f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}", None

    monkeypatch.setattr("phone_runner.identity_proof.tap_copy_link_and_read", mock_tap_copy)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID
    assert copy_calls == 2


def test_identity_copy_root_link_both_attempts_fail(mock_device_env, monkeypatch: pytest.MonkeyPatch):
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        ROOT_COMPOSER_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    copy_calls = 0

    def mock_tap_copy(serial, bounds, sentinel, **kw):
        nonlocal copy_calls
        copy_calls += 1
        return None, "timeout"

    monkeypatch.setattr("phone_runner.identity_proof.tap_copy_link_and_read", mock_tap_copy)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert copy_calls == 2
    assert any("Failed to copy root profile link" in s.get("detail", "") for s in proof.stages)


def test_identity_copy_root_link_retry_deadline_exceeded(mock_device_env, monkeypatch: pytest.MonkeyPatch):
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        ROOT_COMPOSER_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    copy_calls = 0

    def mock_tap_copy(serial, bounds, sentinel, **kw):
        nonlocal copy_calls
        copy_calls += 1
        return None, "timeout"

    monkeypatch.setattr("phone_runner.identity_proof.tap_copy_link_and_read", mock_tap_copy)

    # Advance time after first tap_copy to exceed deadline on retry
    real_monotonic = __import__("time").monotonic
    current_time = real_monotonic()

    def mock_monotonic():
        nonlocal current_time
        if copy_calls >= 1:
            return current_time + 1000.0
        return current_time

    monkeypatch.setattr("phone_runner.identity_proof.time.monotonic", mock_monotonic)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert copy_calls == 1  # Should not have called second time because deadline exceeded before retry
    assert any("copy root link retry" in s.get("detail", "") for s in proof.stages)
