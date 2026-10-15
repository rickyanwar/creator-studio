from __future__ import annotations

from datetime import datetime, timedelta, timezone
import subprocess
from unittest import mock
import pytest

from phone_runner.recovery import (
    A11Y_HELPER_PATH,
    AdbResult,
    check_and_revive_accessibility,
    execute_recovery,
    get_allowed_serials,
    invoke_hermes_repair,
    is_serial_allowed,
    is_serial_valid_format,
    is_valid_reason_code,
    parse_screen_state,
    run_adb,
    run_deterministic_recovery,
    sanitize_error,
    send_owner_notification,
)

TEST_SERIAL = "100.64.96.50:5555"


@pytest.fixture(autouse=True)
def env_setup(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", "secret-test-token")
    monkeypatch.setenv("ADB_PATH", "adb")
    monkeypatch.setenv("HERMES_PATH", "hermes")
    monkeypatch.setenv("HERMES_TIMEOUT_SEC", "30.0")


def test_serial_validation():
    assert is_serial_valid_format(TEST_SERIAL) is True
    assert is_serial_valid_format("emulator-5554") is True
    assert is_serial_valid_format("192.168.1.100:5555") is True

    # Shell injections & illegal chars
    injections = [
        "",
        "   ",
        f"{TEST_SERIAL}; rm -rf /",
        f"{TEST_SERIAL} && reboot",
        "$(whoami)",
        "`id`",
        f"{TEST_SERIAL} | touch /tmp/pwned",
        "dev name with spaces",
    ]
    for bad in injections:
        assert is_serial_valid_format(bad) is False, f"Failed for {bad}"

    # Registered serial check
    assert is_serial_allowed(TEST_SERIAL) is True
    assert is_serial_allowed("192.168.1.99:5555") is False


def test_reason_code_validation():
    valid = [
        "routine_check",
        "device_offline",
        "screen_off",
        "asleep",
        "portal_unready",
        "portal_offline",
        "accessibility_disabled",
        "a11y_disabled",
        "app_not_responding",
        "app_crash",
        "system_unresponsive",
        "connectivity_lost",
        "stale_lease",
        "unknown_failure",
    ]
    for r in valid:
        assert is_valid_reason_code(r) is True

    invalid = [
        "",
        "rm -rf /",
        "$(cat /etc/passwd)",
        "arbitrary command",
        "drop table students",
    ]
    for r in invalid:
        assert is_valid_reason_code(r) is False


def test_screen_state_parsing():
    awake_dumpsys = """
    mWakefulness=Awake
    Display Power: state=ON
    isAwake=true
    """
    assert parse_screen_state(awake_dumpsys) == "awake"

    asleep_dumpsys = """
    mWakefulness=Asleep
    Display Power: state=OFF
    isAwake=false
    """
    assert parse_screen_state(asleep_dumpsys) == "asleep"

    unknown_dumpsys = ""
    assert parse_screen_state(unknown_dumpsys) == "unknown"


def test_deterministic_recovery_already_healthy():
    def fake_runner(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "get-state" in cmd_str:
            return mock.Mock(returncode=0, stdout="device\n", stderr="")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\nDisplay Power: state=ON\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="Row: 0 result=pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="routine_check",
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "resolved"
    assert res["state"] == "resolved"
    assert res["next_state"] == "ready"
    assert res["check_count"] == 1
    assert res["hermes_attempt_count"] == 0
    assert res["error"] is None
    assert len(res["checks"]) >= 3


def test_deterministic_recovery_wakes_sleeping_screen():
    screen_wake_count = 0

    def fake_runner(cmd, **kwargs):
        nonlocal screen_wake_count
        cmd_str = " ".join(cmd)
        if "get-state" in cmd_str:
            return mock.Mock(returncode=0, stdout="device\n", stderr="")
        if "dumpsys power" in cmd_str:
            if screen_wake_count == 0:
                return mock.Mock(returncode=0, stdout="mWakefulness=Asleep\nDisplay Power: state=OFF\n", stderr="")
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\nDisplay Power: state=ON\n", stderr="")
        if "keyevent 224" in cmd_str or "dismiss-keyguard" in cmd_str:
            screen_wake_count += 1
            return mock.Mock(returncode=0, stdout="", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="Row: 0 result=pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="screen_off",
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "resolved"
    assert res["state"] == "resolved"
    assert screen_wake_count >= 1
    # Check that display_state check captured recovery
    display_check = next(c for c in res["checks"] if c["check"] == "display_state")
    assert display_check["status"] == "recovered"


def test_accessibility_revival_helper_not_called_in_test():
    called_commands = []

    def fake_runner(cmd, **kwargs):
        called_commands.append(cmd)
        cmd_str = " ".join(cmd)
        if "get-state" in cmd_str:
            return mock.Mock(returncode=0, stdout="device\n", stderr="")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            # Initially empty, then after put returns mobilerun
            if any("settings put" in " ".join(c) for c in called_commands):
                return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
            return mock.Mock(returncode=0, stdout="\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    chk = check_and_revive_accessibility(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
        allow_helper_script=False,
    )
    assert chk["status"] == "recovered"
    # Verify the shell script was never called
    for cmd in called_commands:
        assert A11Y_HELPER_PATH not in cmd[0]


def test_hermes_fallback_on_unreachable_device_success():
    device_healthy = False
    hermes_called = False

    def fake_runner(cmd, **kwargs):
        nonlocal device_healthy, hermes_called
        cmd_str = " ".join(cmd)
        if cmd[0] == "hermes" and cmd[1] == "heal":
            hermes_called = True
            assert cmd == ["hermes", "heal", "--serial", TEST_SERIAL, "--reason", "device_offline"]
            assert kwargs.get("shell") is False
            device_healthy = True
            return mock.Mock(returncode=0, stdout="device connection healed", stderr="")
        if "get-state" in cmd_str:
            if not device_healthy:
                return mock.Mock(returncode=1, stdout="", stderr="error: device not found")
            return mock.Mock(returncode=0, stdout="device\n", stderr="")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="device_offline",
        subprocess_runner=fake_runner,
    )

    assert hermes_called is True
    assert res["status"] == "resolved"
    assert res["state"] == "resolved"
    assert res["hermes_attempt_count"] == 1


def test_hermes_timeout_handling():
    def fake_runner(cmd, **kwargs):
        if cmd[0] == "hermes" and cmd[1] == "heal":
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30.0)
        return mock.Mock(returncode=1, stdout="", stderr="offline")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="device_offline",
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "needs_human"
    assert res["state"] == "needs_human"
    assert "timed out" in res["error"]
    assert res["hermes_attempt_count"] == 1


def test_notification_command_construction():
    commands_run = []

    def fake_runner(cmd, **kwargs):
        commands_run.append((cmd, kwargs))
        return mock.Mock(returncode=0, stdout="sent", stderr="")

    ok, msg = send_owner_notification("Test notification message", subprocess_runner=fake_runner)
    assert ok is True
    assert len(commands_run) == 1
    argv, kwargs = commands_run[0]
    assert argv == ["hermes", "send", "--to", "telegram", "--message", "Test notification message"]
    assert kwargs.get("shell") is False


def test_routine_exhaustion_reports_needs_human():
    notif_sent = False

    def fake_runner(cmd, **kwargs):
        nonlocal notif_sent
        if cmd[0] == "hermes" and cmd[1] == "send":
            notif_sent = True
            return mock.Mock(returncode=0, stdout="sent", stderr="")
        if cmd[0] == "hermes" and cmd[1] == "heal":
            return mock.Mock(returncode=1, stdout="", stderr="heal failed: port blocked")
        return mock.Mock(returncode=1, stdout="", stderr="offline")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="device_offline",
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "needs_human"
    assert res["state"] == "needs_human"
    assert res["next_state"] == "needs_human"
    assert res["hermes_attempt_count"] == 1
    assert notif_sent is True


def test_larger_repair_first_notice_creates_15min_window():
    notif_calls = []

    def fake_runner(cmd, **kwargs):
        if cmd[0] == "hermes" and cmd[1] == "send":
            notif_calls.append(cmd)
            return mock.Mock(returncode=0, stdout="sent", stderr="")
        return mock.Mock(returncode=1, stdout="", stderr="offline")

    fixed_now = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="larger_repair",
        reason="system_unresponsive",
        now_utc=fixed_now,
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "awaiting_approval"
    assert res["state"] == "awaiting_approval"
    assert res["hermes_attempt_count"] == 0
    assert len(notif_calls) == 1
    expected_deadline = (fixed_now + timedelta(minutes=15)).isoformat()
    assert res["auto_approve_after"] == expected_deadline
    assert res["owner_notified_at"] == fixed_now.isoformat()


def test_larger_repair_awaiting_approval_within_window():
    fixed_now = datetime(2026, 10, 15, 12, 5, 0, tzinfo=timezone.utc)
    deadline = datetime(2026, 10, 15, 12, 15, 0, tzinfo=timezone.utc).isoformat()
    notified_at = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc).isoformat()

    hermes_healed = False

    def fake_runner(cmd, **kwargs):
        nonlocal hermes_healed
        if cmd[0] == "hermes" and cmd[1] == "heal":
            hermes_healed = True
        return mock.Mock(returncode=1, stdout="", stderr="offline")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="larger_repair",
        reason="system_unresponsive",
        auto_approve_after=deadline,
        owner_notified_at=notified_at,
        now_utc=fixed_now,
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "awaiting_approval"
    assert res["state"] == "awaiting_approval"
    assert hermes_healed is False
    assert res["hermes_attempt_count"] == 0


def test_larger_repair_auto_approves_after_silence_past_deadline():
    # 16 minutes after notification
    fixed_now = datetime(2026, 10, 15, 12, 16, 0, tzinfo=timezone.utc)
    deadline = datetime(2026, 10, 15, 12, 15, 0, tzinfo=timezone.utc).isoformat()
    notified_at = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc).isoformat()

    hermes_healed = False

    def fake_runner(cmd, **kwargs):
        nonlocal hermes_healed
        cmd_str = " ".join(cmd)
        if cmd[0] == "hermes" and cmd[1] == "heal":
            hermes_healed = True
            return mock.Mock(returncode=0, stdout="healed", stderr="")
        if "get-state" in cmd_str:
            if hermes_healed:
                return mock.Mock(returncode=0, stdout="device\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="offline")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="larger_repair",
        reason="system_unresponsive",
        auto_approve_after=deadline,
        owner_notified_at=notified_at,
        now_utc=fixed_now,
        subprocess_runner=fake_runner,
    )

    assert hermes_healed is True
    assert res["status"] == "resolved"
    assert res["state"] == "resolved"


def test_larger_repair_owner_rejection_blocks_repair():
    hermes_called = False

    def fake_runner(cmd, **kwargs):
        nonlocal hermes_called
        if cmd[0] == "hermes" and cmd[1] == "heal":
            hermes_called = True
        return mock.Mock(returncode=1, stdout="", stderr="offline")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="larger_repair",
        reason="system_unresponsive",
        owner_decision="rejected",
        subprocess_runner=fake_runner,
    )

    assert res["status"] == "needs_human"
    assert res["state"] == "needs_human"
    assert res["owner_decision"] == "rejected"
    assert hermes_called is False
    assert res["hermes_attempt_count"] == 0


def test_larger_repair_owner_approval_proceeds():
    hermes_called = False

    def fake_runner(cmd, **kwargs):
        nonlocal hermes_called
        cmd_str = " ".join(cmd)
        if cmd[0] == "hermes" and cmd[1] == "heal":
            hermes_called = True
            return mock.Mock(returncode=0, stdout="healed", stderr="")
        if "get-state" in cmd_str:
            if hermes_called:
                return mock.Mock(returncode=0, stdout="device\n", stderr="")
            return mock.Mock(returncode=1, stdout="", stderr="offline")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="larger_repair",
        reason="system_unresponsive",
        owner_decision="approved",
        subprocess_runner=fake_runner,
    )

    assert hermes_called is True
    assert res["status"] == "resolved"
    assert res["state"] == "resolved"


def test_safe_error_sanitization():
    raw_error = "Error: Bearer abcdef1234567890 token=secret_value_xyz failed at 0xdeadbeef"
    clean = sanitize_error(raw_error)
    assert "abcdef1234567890" not in clean
    assert "secret_value_xyz" not in clean
    assert "[REDACTED]" in clean

    # Long string truncated
    long_msg = "x" * 500
    clean_long = sanitize_error(long_msg)
    assert len(clean_long) <= 256
    assert clean_long.endswith("...")


def test_reject_unregistered_serial():
    res = execute_recovery(
        serial="192.168.1.99:5555",
        recovery_class="routine",
        reason="routine_check",
    )
    assert res["status"] == "failed"
    assert res["error"] == "serial_not_allowed"


def test_reject_invalid_reason():
    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="arbitrary; rm -rf /",
    )
    assert res["status"] == "failed"
    assert res["error"] == "invalid_reason"


def test_reject_invalid_recovery_class():
    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="unsupported_class",
        reason="routine_check",
    )
    assert res["status"] == "failed"
    assert res["error"] == "invalid_recovery_class"


def test_reject_shell_injection_serial():
    bad_serials = [
        f"{TEST_SERIAL}; reboot",
        f"{TEST_SERIAL} && cat /etc/passwd",
        "$(whoami)",
        "`id`",
    ]
    for s in bad_serials:
        res = execute_recovery(
            serial=s,
            recovery_class="routine",
            reason="routine_check",
        )
        assert res["status"] == "failed"
        assert res["error"] == "invalid_serial_format"


def test_accessibility_helper_script_allowed_path():
    called = []

    def fake_runner(cmd, **kwargs):
        called.append(cmd)
        cmd_str = " ".join(cmd)
        if "settings get" in cmd_str:
            return mock.Mock(returncode=1, stdout="", stderr="")
        if "settings put" in cmd_str:
            return mock.Mock(returncode=1, stdout="", stderr="")
        if cmd[0] == A11Y_HELPER_PATH:
            return mock.Mock(returncode=0, stdout="success", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    with mock.patch("os.path.isfile", return_value=True), mock.patch("os.access", return_value=True):
        res = check_and_revive_accessibility(
            serial=TEST_SERIAL,
            subprocess_runner=fake_runner,
            allow_helper_script=True,
        )
        assert res["status"] == "recovered"
        assert res["detail"] == "revived via a11y helper"
        # Must be strictly serial-bound with no shell
        assert any(cmd == [A11Y_HELPER_PATH, "-s", TEST_SERIAL] for cmd in called)
