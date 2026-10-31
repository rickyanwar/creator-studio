from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
from unittest import mock
import pytest

from phone_runner.recovery import (
    A11Y_HELPER_PATH,
    PORTAL_A11Y_COMPONENT,
    AdbResult,
    check_and_revive_accessibility,
    execute_recovery,
    find_dialog_button,
    find_portal_switch,
    get_allowed_serials,
    invoke_hermes_repair,
    is_serial_allowed,
    is_serial_valid_format,
    is_valid_reason_code,
    parse_screen_state,
    revive_accessibility_via_settings_ui,
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
    monkeypatch.setenv("RECOVERY_SETTLE_SEC", "0")
    monkeypatch.delenv("HERMES_OUTSIDE_SANDBOX", raising=False)


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
        if cmd[0] == "hermes" and cmd[1] == "-z":
            hermes_called = True
            assert cmd[:2] == ["hermes", "-z"] and TEST_SERIAL in cmd[2] and "device_offline" in cmd[2] and "do NOT open" in cmd[2]
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
        if cmd[0] == "hermes" and cmd[1] == "-z":
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
    assert argv == ["hermes", "send", "-t", "telegram", "Test notification message"]
    assert kwargs.get("shell") is False


def test_routine_exhaustion_reports_needs_human():
    notif_sent = False

    def fake_runner(cmd, **kwargs):
        nonlocal notif_sent
        if cmd[0] == "hermes" and cmd[1] == "send":
            notif_sent = True
            return mock.Mock(returncode=0, stdout="sent", stderr="")
        if cmd[0] == "hermes" and cmd[1] == "-z":
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
        if cmd[0] == "hermes" and cmd[1] == "-z":
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
        if cmd[0] == "hermes" and cmd[1] == "-z":
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
        if cmd[0] == "hermes" and cmd[1] == "-z":
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
        if cmd[0] == "hermes" and cmd[1] == "-z":
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


FIXTURES_DIR = Path(__file__).parent / "fixtures"
DETAILS_XML = (FIXTURES_DIR / "a11y_details_page.xml").read_text(encoding="utf-8")
DIALOG_XML = (FIXTURES_DIR / "a11y_allow_dialog.xml").read_text(encoding="utf-8")


def test_find_portal_switch():
    res = find_portal_switch(DETAILS_XML)
    assert res == (927, 410, False)

    target = 'switch_widget" class="android.widget.Switch" package="com.android.settings" content-desc="" checkable="true" checked="false"'
    replacement = 'switch_widget" class="android.widget.Switch" package="com.android.settings" content-desc="" checkable="true" checked="true"'
    checked_xml = DETAILS_XML.replace(target, replacement)
    res_checked = find_portal_switch(checked_xml)
    assert res_checked == (927, 410, True)

    no_title_xml = DETAILS_XML.replace("Mobilerun Portal", "Other App")
    assert find_portal_switch(no_title_xml) is None

    assert find_portal_switch("") is None
    assert find_portal_switch("not xml") is None


def test_find_dialog_button():
    res = find_dialog_button(DIALOG_XML)
    assert res == (786, 2117)

    assert find_dialog_button(DIALOG_XML, labels=("deny", "DENY", "Deny")) is None
    assert find_dialog_button(DIALOG_XML, labels=("CustomNonExistent",)) is None
    assert find_dialog_button("") is None


def test_append_semantics():
    called = []

    def fake_runner_existing(cmd, **kwargs):
        called.append(cmd)
        cmd_str = " ".join(cmd)
        if "settings get" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.other/.Svc\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    check_and_revive_accessibility(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner_existing,
        allow_ui_revive=False,
    )
    put_cmd = next(c for c in called if "settings" in c and "put" in c and "enabled_accessibility_services" in c)
    assert put_cmd[-1] == f"com.other/.Svc:{PORTAL_A11Y_COMPONENT}"

    called_null = []

    def fake_runner_null(cmd, **kwargs):
        called_null.append(cmd)
        cmd_str = " ".join(cmd)
        if "settings get" in cmd_str:
            return mock.Mock(returncode=0, stdout="null\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    check_and_revive_accessibility(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner_null,
        allow_ui_revive=False,
    )
    put_null_cmd = next(c for c in called_null if "settings" in c and "put" in c and "enabled_accessibility_services" in c)
    assert put_null_cmd[-1] == PORTAL_A11Y_COMPONENT


def test_security_exception_on_put_ui_revive_disabled():
    def fake_runner(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "settings get" in cmd_str:
            return mock.Mock(returncode=0, stdout="null\n", stderr="")
        if "settings put" in cmd_str:
            return mock.Mock(
                returncode=0,
                stdout="",
                stderr="java.lang.SecurityException: Permission denial, must have one of: [android.permission.WRITE_SECURE_SETTINGS]\n",
            )
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = check_and_revive_accessibility(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
        allow_ui_revive=False,
    )
    assert res["status"] == "failed"
    assert res["error_code"] == "secure_settings_blocked"
    assert res["detail"] == (
        "secure settings write blocked by the OS (ColorOS permission monitoring); "
        "enable it on the phone: Settings > Accessibility > Mobilerun Portal > On, "
        "or Developer options > Disable permission monitoring"
    )


def test_security_exception_on_put_ui_revive_succeeds():
    called = []
    dump_count = 0

    def fake_runner(cmd, **kwargs):
        nonlocal dump_count
        called.append(cmd)
        cmd_str = " ".join(cmd)
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Asleep\n", stderr="")
        if "settings get" in cmd_str:
            if any(c[-4:] == ["input", "tap", "786", "2117"] for c in called):
                return mock.Mock(returncode=0, stdout=f"{PORTAL_A11Y_COMPONENT}\n", stderr="")
            return mock.Mock(returncode=0, stdout="null\n", stderr="")
        if "settings put" in cmd_str:
            return mock.Mock(
                returncode=0,
                stdout="",
                stderr="java.lang.SecurityException: Permission denial, must have one of: [android.permission.WRITE_SECURE_SETTINGS]\n",
            )
        if "dumpsys window" in cmd_str:
            return mock.Mock(returncode=0, stdout="mCurrentFocus=Window{123 u0 com.android.settings/.Settings}\n", stderr="")
        if "uiautomator dump" in cmd_str:
            dump_count += 1
            return mock.Mock(returncode=0, stdout="UI hierarchy dumped to: /sdcard/...", stderr="")
        if "cat" in cmd_str:
            if dump_count <= 1:
                return mock.Mock(returncode=0, stdout=DETAILS_XML, stderr="")
            return mock.Mock(returncode=0, stdout=DIALOG_XML, stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = check_and_revive_accessibility(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
        allow_ui_revive=True,
    )
    assert res["status"] == "recovered"
    assert res["detail"] == "accessibility enabled via Settings UI"

    switch_taps = [c for c in called if c[-4:] == ["input", "tap", "927", "410"]]
    allow_taps = [c for c in called if c[-4:] == ["input", "tap", "786", "2117"]]
    assert len(switch_taps) == 1
    assert len(allow_taps) == 1

    keyevent_cmds = [c for c in called if "keyevent" in c]
    assert any(c[-3:] == ["input", "keyevent", "3"] for c in keyevent_cmds)
    assert any(c[-3:] == ["input", "keyevent", "223"] for c in keyevent_cmds)
    assert called[-1][-3:] == ["input", "keyevent", "223"]


def test_secure_lock_ui_revive_returns_false_no_taps():
    called = []

    def fake_runner(cmd, **kwargs):
        called.append(cmd)
        cmd_str = " ".join(cmd)
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "dumpsys window" in cmd_str:
            return mock.Mock(returncode=0, stdout="mCurrentFocus=Window{cb5bf64 u0 NotificationShade}\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    ok = revive_accessibility_via_settings_ui(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
    )
    assert ok is False
    assert not any("tap" in c for c in called)


def test_run_deterministic_recovery_sleep_restoration_success():
    called = []
    screen_wake_count = 0

    def fake_runner(cmd, **kwargs):
        nonlocal screen_wake_count
        called.append(cmd)
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
            return mock.Mock(returncode=0, stdout=f"{PORTAL_A11Y_COMPONENT}\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    ok, checks, err = run_deterministic_recovery(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
        restore_sleep=True,
    )
    assert ok is True
    assert called[-1][-3:] == ["input", "keyevent", "223"]


def test_run_deterministic_recovery_sleep_restoration_failure():
    called = []
    screen_wake_count = 0

    def fake_runner(cmd, **kwargs):
        nonlocal screen_wake_count
        called.append(cmd)
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
            return mock.Mock(returncode=1, stdout="", stderr="error")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=1, stdout="", stderr="error")
        return mock.Mock(returncode=0, stdout="", stderr="")

    ok, checks, err = run_deterministic_recovery(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
        restore_sleep=True,
    )
    assert ok is False
    assert called[-1][-3:] == ["input", "keyevent", "223"]


def test_run_deterministic_recovery_restore_sleep_false():
    called = []
    screen_wake_count = 0

    def fake_runner(cmd, **kwargs):
        nonlocal screen_wake_count
        called.append(cmd)
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
            return mock.Mock(returncode=0, stdout=f"{PORTAL_A11Y_COMPONENT}\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    ok, checks, err = run_deterministic_recovery(
        serial=TEST_SERIAL,
        subprocess_runner=fake_runner,
        restore_sleep=False,
    )
    assert ok is True
    assert not any(c[-3:] == ["input", "keyevent", "223"] for c in called)


def test_hermes_runs_outside_the_sandbox_under_the_unit(monkeypatch):
    from phone_runner import recovery

    monkeypatch.setenv("HERMES_PATH", "/root/.local/bin/hermes")
    monkeypatch.setenv("HERMES_OUTSIDE_SANDBOX", "1")
    cmd = recovery.hermes_command(["send", "-t", "telegram", "hi"], 15.0)
    assert cmd[:5] == ["systemd-run", "--quiet", "--wait", "--pipe", "--collect"]
    assert cmd[-5:] == ["/root/.local/bin/hermes", "send", "-t", "telegram", "hi"]
    monkeypatch.delenv("HERMES_OUTSIDE_SANDBOX")
    assert recovery.hermes_command(["send"], 15.0) == ["/root/.local/bin/hermes", "send"]


def test_late_recheck_resolves_instead_of_false_needs_human(monkeypatch):
    from phone_runner import recovery

    results = iter([(False, [{"check": "portal", "status": "failed"}], "portal not ready"),
                    (True, [{"check": "portal", "status": "ok"}], None)])
    monkeypatch.setattr(recovery, "run_deterministic_recovery", lambda *a, **k: next(results))
    monkeypatch.setattr(recovery, "invoke_hermes_repair", lambda *a, **k: (False, "hermes healing failed"))
    sent = []
    monkeypatch.setattr(recovery, "send_owner_notification", lambda msg, **k: sent.append(msg) or (True, "sent"))

    res = recovery.execute_recovery(TEST_SERIAL, recovery_class="routine", reason="portal_unready")

    assert res["status"] == "resolved"
    assert sent == []  # no Telegram page for a device that recovered


def test_repair_prompt_forbids_facebook_and_posting():
    from phone_runner.recovery import build_hermes_repair_prompt

    prompt = build_hermes_repair_prompt(TEST_SERIAL, "portal_unready")
    assert TEST_SERIAL in prompt and "content://com.mobilerun.portal/ping" in prompt
    assert "do NOT open, tap, type in or change Facebook" in prompt and "never post" in prompt


def test_vision_transient_codes_accepted_by_is_valid_reason_code():
    assert is_valid_reason_code("vision_unavailable") is True
    assert is_valid_reason_code("time_budget_exceeded") is True


def test_vision_transient_codes_rejected_values_unchanged():
    # Malformed and unrelated values must still be rejected.
    still_invalid = [
        "",
        "vision unavailable",      # space — fails REASON_REGEX
        "time-budget exceeded",    # space — fails REASON_REGEX
        "VISION_UNAVAILABLE",      # wrong case (not in set)
        "vision_unavailable ",     # trailing space
        " time_budget_exceeded",   # leading space
        "arbitrary_code",
        "rm -rf /",
    ]
    for v in still_invalid:
        assert is_valid_reason_code(v) is False, f"Expected False for {v!r}"


def test_execute_recovery_vision_unavailable_does_not_return_invalid_reason():
    """vision_unavailable must pass the reason-code gate and reach deterministic recovery."""
    def fake_runner(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "get-state" in cmd_str:
            return mock.Mock(returncode=0, stdout="device\n", stderr="")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="Row: 0 result=pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="vision_unavailable",
        subprocess_runner=fake_runner,
    )
    assert res.get("error") != "invalid_reason", "vision_unavailable must not hit invalid_reason gate"
    assert res["status"] == "resolved"


def test_execute_recovery_time_budget_exceeded_does_not_return_invalid_reason():
    """time_budget_exceeded must pass the reason-code gate and reach deterministic recovery."""
    def fake_runner(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        if "get-state" in cmd_str:
            return mock.Mock(returncode=0, stdout="device\n", stderr="")
        if "dumpsys power" in cmd_str:
            return mock.Mock(returncode=0, stdout="mWakefulness=Awake\n", stderr="")
        if "content query" in cmd_str:
            return mock.Mock(returncode=0, stdout="Row: 0 result=pong\n", stderr="")
        if "settings get secure enabled_accessibility_services" in cmd_str:
            return mock.Mock(returncode=0, stdout="com.mobilerun.portal/.AccessibilityService\n", stderr="")
        return mock.Mock(returncode=0, stdout="", stderr="")

    res = execute_recovery(
        serial=TEST_SERIAL,
        recovery_class="routine",
        reason="time_budget_exceeded",
        subprocess_runner=fake_runner,
    )
    assert res.get("error") != "invalid_reason", "time_budget_exceeded must not hit invalid_reason gate"
    assert res["status"] == "resolved"
