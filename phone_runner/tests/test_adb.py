from unittest import mock

import pytest

from phone_runner.adb import (
    AdbResult,
    is_device_secure_locked,
    parse_screen_state,
    put_to_sleep,
    query_ui_hierarchy,
    wake_and_unlock,
)

TEST_SERIAL = "100.64.96.50:5555"


def test_put_to_sleep_uses_deterministic_keycode_223():
    """Verify put_to_sleep invokes KEYCODE_SLEEP 223, not power-toggle 26."""
    recorded_args = []

    def fake_run_adb(args, serial=None, timeout=None):
        recorded_args.append((args, serial))
        return AdbResult(returncode=0, stdout="OK", stderr="")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = put_to_sleep(TEST_SERIAL)

    assert res.returncode == 0
    assert len(recorded_args) == 1
    args, serial = recorded_args[0]
    assert serial == TEST_SERIAL
    assert args == ["shell", "input", "keyevent", "223"]
    assert "26" not in args


def test_put_to_sleep_safe_for_already_asleep_device():
    """Verify put_to_sleep behavior when device is already asleep.

    KEYCODE_SLEEP 223 is idempotent; on an already-asleep display it maintains
    sleep rather than toggling power back to awake like KEYCODE_POWER 26.
    """
    dumpsys_asleep = "mWakefulness=Asleep\nmHoldingWakeLockSuspendBlocker=false"
    state = parse_screen_state(dumpsys_asleep)
    assert state == "asleep"

    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        return AdbResult(returncode=0, stdout="", stderr="")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = put_to_sleep(TEST_SERIAL)

    assert res.returncode == 0
    assert calls == [["shell", "input", "keyevent", "223"]]


def test_put_to_sleep_safe_for_awake_device():
    """Verify put_to_sleep behavior when device is currently awake."""
    dumpsys_awake = "mWakefulness=Awake\nmHoldingWakeLockSuspendBlocker=true"
    state = parse_screen_state(dumpsys_awake)
    assert state == "awake"

    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        return AdbResult(returncode=0, stdout="", stderr="")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = put_to_sleep(TEST_SERIAL)

    assert res.returncode == 0
    assert calls == [["shell", "input", "keyevent", "223"]]


def test_wake_and_unlock_uses_wakeup_and_dismiss_keyguard():
    """Verify wake_and_unlock invokes 224 followed by wm dismiss-keyguard."""
    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        return AdbResult(returncode=0, stdout="", stderr="")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = wake_and_unlock(TEST_SERIAL)

    assert res.returncode == 0
    assert len(calls) == 2
    assert calls[0] == ["shell", "input", "keyevent", "224"]
    assert calls[1] == ["shell", "wm", "dismiss-keyguard"]


# ---------------------------------------------------------------------------
# query_ui_hierarchy tests
# ---------------------------------------------------------------------------

VALID_HIERARCHY_XML = "<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation=\"0\"><node index=\"0\" text=\"Hello\" /></hierarchy>"


def test_query_ui_hierarchy_success():
    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(0, "UI hierchary dumped to: /data/local/tmp/uidump_123.xml", "")
        if args[:2] == ["shell", "cat"]:
            return AdbResult(0, VALID_HIERARCHY_XML, "")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "unexpected")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode == 0
    assert res.stdout == VALID_HIERARCHY_XML
    assert res.stderr == ""
    assert len(calls) == 3
    assert calls[0][:3] == ["shell", "uiautomator", "dump"]
    assert calls[1][:2] == ["shell", "cat"]
    assert calls[2][:3] == ["shell", "rm", "-f"]


def test_query_ui_hierarchy_dump_rc_nonzero():
    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(1, "", "uiautomator failed")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "cat should not be called")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode != 0
    assert res.stdout == ""
    assert "dump failed" in res.stderr
    assert not any(c[:2] == ["shell", "cat"] for c in calls)
    assert any(c[:3] == ["shell", "rm", "-f"] for c in calls)


def test_query_ui_hierarchy_dump_rc0_with_error():
    """Live testing reproduced rc0 dump with ERROR output."""
    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(0, "ERROR: null root node returned by UiTestAutomationBridge", "")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "cat should not be called")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode != 0
    assert res.stdout == ""
    assert "dump failed" in res.stderr
    assert not any(c[:2] == ["shell", "cat"] for c in calls)
    assert any(c[:3] == ["shell", "rm", "-f"] for c in calls)


def test_query_ui_hierarchy_cat_failure():
    calls = []

    def fake_run_adb(args, serial=None, timeout=None):
        calls.append(args)
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(0, "UI hierchary dumped to: ...", "")
        if args[:2] == ["shell", "cat"]:
            return AdbResult(1, "", "cat: /data/local/tmp/...: No such file")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "unexpected")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode != 0
    assert res.stdout == ""
    assert "read failed" in res.stderr
    assert any(c[:3] == ["shell", "rm", "-f"] for c in calls)


def test_query_ui_hierarchy_empty_xml():
    def fake_run_adb(args, serial=None, timeout=None):
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(0, "UI hierchary dumped to: ...", "")
        if args[:2] == ["shell", "cat"]:
            return AdbResult(0, "   \n\t", "")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "unexpected")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode != 0
    assert res.stdout == ""
    assert "empty" in res.stderr


def test_query_ui_hierarchy_malformed_xml():
    malformed = "<?xml version='1.0' ?><hierarchy><node unclosed></hierarchy"

    def fake_run_adb(args, serial=None, timeout=None):
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(0, "UI hierchary dumped to: ...", "")
        if args[:2] == ["shell", "cat"]:
            return AdbResult(0, malformed, "")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "unexpected")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode != 0
    assert res.stdout == ""
    assert "malformed" in res.stderr
    # Must not leak raw XML in error details
    assert "<node unclosed>" not in res.stderr


def test_query_ui_hierarchy_unique_paths():
    dump_paths = []

    def fake_run_adb(args, serial=None, timeout=None):
        if args[:3] == ["shell", "uiautomator", "dump"]:
            dump_paths.append(args[3])
            return AdbResult(0, "", "")
        if args[:2] == ["shell", "cat"]:
            return AdbResult(0, VALID_HIERARCHY_XML, "")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(0, "", "")
        return AdbResult(-1, "", "")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        query_ui_hierarchy(TEST_SERIAL)
        query_ui_hierarchy(TEST_SERIAL)

    assert len(dump_paths) == 2
    assert dump_paths[0] != dump_paths[1]
    assert dump_paths[0].startswith("/data/local/tmp/uidump_")
    assert dump_paths[1].startswith("/data/local/tmp/uidump_")


def test_query_ui_hierarchy_cleanup_failure():
    def fake_run_adb(args, serial=None, timeout=None):
        if args[:3] == ["shell", "uiautomator", "dump"]:
            return AdbResult(0, "", "")
        if args[:2] == ["shell", "cat"]:
            return AdbResult(0, VALID_HIERARCHY_XML, "")
        if args[:3] == ["shell", "rm", "-f"]:
            return AdbResult(1, "", "rm: permission denied")
        return AdbResult(-1, "", "")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res = query_ui_hierarchy(TEST_SERIAL)

    assert res.returncode != 0
    assert res.stdout == ""
    assert "cleanup failed" in res.stderr


# ---------------------------------------------------------------------------
# is_device_secure_locked tests
# ---------------------------------------------------------------------------

def test_is_device_secure_locked_realme_secure_and_showing():
    dumpsys_out = """
WINDOW MANAGER POLICY STATE (dumpsys window policy)
  KeyguardServiceDelegate
    showing=true
    showingAndNotOccluded=true
    inputRestricted=true
    occluded=false
    secure=true
    deviceHasKeyguard=true
    enabled=true
"""
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, dumpsys_out, "")):
        assert is_device_secure_locked(TEST_SERIAL) is True


def test_is_device_secure_locked_swipe_lock():
    dumpsys_out = """
WINDOW MANAGER POLICY STATE (dumpsys window policy)
  KeyguardServiceDelegate
    showing=true
    showingAndNotOccluded=true
    secure=false
    deviceHasKeyguard=true
"""
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, dumpsys_out, "")):
        assert is_device_secure_locked(TEST_SERIAL) is False


def test_is_device_secure_locked_unlocked_secure():
    dumpsys_out = """
WINDOW MANAGER POLICY STATE (dumpsys window policy)
  KeyguardServiceDelegate
    showing=false
    showingAndNotOccluded=false
    secure=true
    deviceHasKeyguard=true
"""
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, dumpsys_out, "")):
        assert is_device_secure_locked(TEST_SERIAL) is False


def test_is_device_secure_locked_legacy_formats():
    # Legacy format 1: mShowingLockscreen and isKeyguardSecure
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "mShowingLockscreen=true isKeyguardSecure=true", "")):
        assert is_device_secure_locked(TEST_SERIAL) is True
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "mShowingLockscreen=false isKeyguardSecure=true", "")):
        assert is_device_secure_locked(TEST_SERIAL) is False
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "mShowingLockscreen=true isKeyguardSecure=false", "")):
        assert is_device_secure_locked(TEST_SERIAL) is False

    # Legacy format 2: mKeyguardShowing and mKeyguardSecure
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "mKeyguardShowing=true mKeyguardSecure=true", "")):
        assert is_device_secure_locked(TEST_SERIAL) is True
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "mKeyguardShowing=false mKeyguardSecure=true", "")):
        assert is_device_secure_locked(TEST_SERIAL) is False
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "mKeyguardShowing=true mKeyguardSecure=false", "")):
        assert is_device_secure_locked(TEST_SERIAL) is False


def test_is_device_secure_locked_failed_read():
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(-1, "", "adb timeout")):
        assert is_device_secure_locked(TEST_SERIAL) is True


def test_is_device_secure_locked_empty_ambiguous():
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "", "")):
        assert is_device_secure_locked(TEST_SERIAL) is True

    ambiguous = "KeyguardServiceDelegate\n showing=true\n"
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, ambiguous, "")):
        assert is_device_secure_locked(TEST_SERIAL) is True


def test_is_device_secure_locked_misleading_fields_outside_section():
    # Showing=false outside, but KeyguardServiceDelegate has showing=true, secure=true
    dumpsys_misleading = """
DisplayPolicy:
  showing=false
  secure=false
KeyguardServiceDelegate:
  showing=true
  secure=true
"""
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, dumpsys_misleading, "")):
        assert is_device_secure_locked(TEST_SERIAL) is True

    # Showing=true outside, but KeyguardServiceDelegate has showing=false, secure=true
    dumpsys_unlocked = """
DisplayPolicy:
  showing=true
  secure=true
KeyguardServiceDelegate:
  showing=false
  secure=true
"""
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, dumpsys_unlocked, "")):
        assert is_device_secure_locked(TEST_SERIAL) is False


def test_is_device_secure_locked_conflicts_fail_closed():
    dumpsys_conflict = """
KeyguardServiceDelegate:
  showing=true
  showing=false
  secure=true
"""
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, dumpsys_conflict, "")):
        assert is_device_secure_locked(TEST_SERIAL) is True
