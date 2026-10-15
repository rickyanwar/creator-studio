import pytest
from unittest.mock import Mock

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import verify_foreground_facebook
from phone_runner.mobilerun import PORTAL_IME_COMPONENT, restore_device_ime

TEST_SERIAL = "TEST_SERIAL_1234"
GBOARD_IME = "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"

FB_XML_MINIMAL = """<?xml version="1.0" encoding="utf-8"?>
<hierarchy rotation="0">
  <node package="com.facebook.katana" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]" clickable="false">
    <node package="com.facebook.katana" class="android.view.View" bounds="[0,100][1080,2300]" clickable="true" text="Feed" />
  </node>
</hierarchy>"""


def test_restore_ime_incoming_portal_enabled_and_default(monkeypatch):
    """When incoming Portal was enabled and default, enables Portal before setting default."""
    calls = []

    def mock_adb(args, serial=None):
        calls.append(list(args))
        if args == ["shell", "ime", "enable", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "Input method enabled\n", "")
        if args == ["shell", "ime", "set", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "Input method selected\n", "")
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{PORTAL_IME_COMPONENT}\n", "")
        if args == ["shell", "ime", "list", "-s"]:
            return AdbResult(0, f"{GBOARD_IME}\n{PORTAL_IME_COMPONENT}\n", "")
        raise AssertionError(f"Unexpected adb call: {args}")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb)

    ok, err = restore_device_ime(TEST_SERIAL, PORTAL_IME_COMPONENT, portal_was_enabled=True)
    assert ok is True
    assert err == ""

    # Verify enable was called BEFORE set
    enable_idx = calls.index(["shell", "ime", "enable", PORTAL_IME_COMPONENT])
    set_idx = calls.index(["shell", "ime", "set", PORTAL_IME_COMPONENT])
    assert enable_idx < set_idx


def test_restore_ime_incoming_gboard_portal_disabled(monkeypatch):
    """When incoming Portal was disabled, sets default first then disables Portal."""
    calls = []

    def mock_adb(args, serial=None):
        calls.append(list(args))
        if args == ["shell", "ime", "set", GBOARD_IME]:
            return AdbResult(0, "Input method selected\n", "")
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        if args == ["shell", "ime", "disable", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "Input method disabled\n", "")
        if args == ["shell", "ime", "list", "-s"]:
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        raise AssertionError(f"Unexpected adb call: {args}")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb)

    ok, err = restore_device_ime(TEST_SERIAL, GBOARD_IME, portal_was_enabled=False)
    assert ok is True
    assert err == ""

    set_idx = calls.index(["shell", "ime", "set", GBOARD_IME])
    dis_idx = calls.index(["shell", "ime", "disable", PORTAL_IME_COMPONENT])
    assert set_idx < dis_idx


def test_restore_ime_failed_enable_fails_sanitized(monkeypatch):
    """Failure to enable Portal returns sanitized error message without leaking raw details."""
    def mock_adb(args, serial=None):
        if args == ["shell", "ime", "enable", PORTAL_IME_COMPONENT]:
            return AdbResult(1, "", "Error: unknown component")
        return AdbResult(0, "", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb)

    ok, err = restore_device_ime(TEST_SERIAL, PORTAL_IME_COMPONENT, portal_was_enabled=True)
    assert ok is False
    assert err == "Failed to execute IME enable command"


def test_restore_ime_failed_enabled_list_fails_sanitized(monkeypatch):
    """Nonzero returncode from final ime list -s fails sanitized in both branches."""
    def mock_adb_portal(args, serial=None):
        if args == ["shell", "ime", "enable", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "ime", "set", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{PORTAL_IME_COMPONENT}\n", "")
        if args == ["shell", "ime", "list", "-s"]:
            return AdbResult(1, "", "Device failure")
        return AdbResult(0, "", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb_portal)
    ok, err = restore_device_ime(TEST_SERIAL, PORTAL_IME_COMPONENT, portal_was_enabled=True)
    assert ok is False
    assert "list" in err.lower() or "query" in err.lower()


def test_restore_ime_readback_mismatch_fails_sanitized(monkeypatch):
    """Default IME readback mismatch returns sanitized error."""
    def mock_adb(args, serial=None):
        if args == ["shell", "ime", "enable", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "ime", "set", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        return AdbResult(0, "", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb)
    ok, err = restore_device_ime(TEST_SERIAL, PORTAL_IME_COMPONENT, portal_was_enabled=True)
    assert ok is False
    assert err == "Default IME readback mismatch during restoration"


def test_restore_ime_final_state_mismatch_fails(monkeypatch):
    """If final enabled state does not match snapshot, returns sanitized error."""
    # Was enabled, but list does not contain portal
    def mock_adb_missing_portal(args, serial=None):
        if args == ["shell", "ime", "enable", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "ime", "set", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{PORTAL_IME_COMPONENT}\n", "")
        if args == ["shell", "ime", "list", "-s"]:
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        return AdbResult(0, "", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb_missing_portal)
    ok, err = restore_device_ime(TEST_SERIAL, PORTAL_IME_COMPONENT, portal_was_enabled=True)
    assert ok is False
    assert "Portal IME not enabled" in err


def test_verify_foreground_fb_focus_with_first_historic_gallery_passes(monkeypatch):
    """currentFocus FB + focusedApp FB + first historic Gallery ACTIVITY (non-running) + FB XML passes."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    win_out = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, win_out, ""))

    act_out = (
        "Stack #1: type=standard\n"
        "  Task id #10\n"
        "    ACTIVITY com.coloros.gallery3d/.app.Gallery 12345 pid=(not running)\n"
        "  Task id #9\n"
        "    ACTIVITY com.android.settings/.Settings 12344 pid=456\n"
        "  Task id #8\n"
        "    ACTIVITY com.facebook.katana/.activity.FbMainTabActivity 12343 pid=789\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, act_out, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, FB_XML_MINIMAL, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is True
    assert err == ""
    assert tree is not None


def test_verify_foreground_missing_focus_with_historic_fb_rejects(monkeypatch):
    """Missing mCurrentFocus even with historic FB in activity dump must reject."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0\n", ""))

    act_out = (
        "Stack #1:\n"
        "  ACTIVITY com.facebook.katana/.activity.FbMainTabActivity 12343 pid=789\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, act_out, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, FB_XML_MINIMAL, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "focus" in err.lower()


def test_verify_foreground_gallery_current_focus_rejects(monkeypatch):
    """Gallery currentFocus even with historic FB in activity dump must reject."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    win_out = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{12345 u0 com.coloros.gallery3d/com.coloros.gallery3d.app.MainActivity}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, win_out, ""))

    act_out = (
        "Stack #1:\n"
        "  ACTIVITY com.facebook.katana/.activity.FbMainTabActivity 12343 pid=789\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, act_out, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, FB_XML_MINIMAL, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "com.coloros.gallery3d" in err or "focus" in err.lower()


def test_verify_foreground_fb_focus_with_other_focused_app_rejects(monkeypatch):
    """FB currentFocus with other focusedApp contradicts focus and must reject."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    win_out = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.android.launcher3/...}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, win_out, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, FB_XML_MINIMAL, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "contradictory" in err.lower() or "focus" in err.lower()


def test_verify_foreground_explicit_resumed_marker_other_rejects(monkeypatch):
    """Unambiguous explicit resumed marker for non-FB package must reject."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    win_out = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, win_out, ""))

    act_out = (
        "topResumedActivity=ActivityRecord{a1b2c3 u0 com.android.launcher3/.Launcher t10}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, act_out, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, FB_XML_MINIMAL, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "com.android.launcher3" in err


def test_verify_foreground_window_query_returncode_error(monkeypatch):
    """Nonzero returncode from query_window_state rejects immediately."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(1, "", "Failed to connect to wm"))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "window" in err.lower()


def test_restore_ime_lookalike_portal_component_rejected(monkeypatch):
    """Substring match like com.mobilerun.portal.lookalike does not satisfy exact PORTAL_IME_COMPONENT."""
    from phone_runner.mobilerun import snapshot_device_ime

    lookalike = "com.mobilerun.portal.lookalike/.FakeIME"

    def mock_adb_snapshot(args, serial=None):
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        if args == ["shell", "ime", "list", "-s"]:
            return AdbResult(0, f"{lookalike}\n", "")
        raise AssertionError(f"Unexpected adb call: {args}")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb_snapshot)
    ok, orig_default, portal_enabled = snapshot_device_ime(TEST_SERIAL)
    assert ok is True
    assert orig_default == GBOARD_IME
    assert portal_enabled is False

    # During restoration with portal_was_enabled=True, lookalike does not satisfy requirement
    def mock_adb_restore(args, serial=None):
        if args == ["shell", "ime", "enable", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "ime", "set", PORTAL_IME_COMPONENT]:
            return AdbResult(0, "OK", "")
        if args == ["shell", "settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{PORTAL_IME_COMPONENT}\n", "")
        if args == ["shell", "ime", "list", "-s"]:
            return AdbResult(0, f"{lookalike}\n", "")
        raise AssertionError(f"Unexpected adb call: {args}")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", mock_adb_restore)
    ok_res, err_res = restore_device_ime(TEST_SERIAL, PORTAL_IME_COMPONENT, portal_was_enabled=True)
    assert ok_res is False
    assert "Portal IME not enabled" in err_res


def test_verify_foreground_first_fb_resumed_then_other_contradiction_rejects(monkeypatch):
    """Multiple explicit resumed markers reject if ANY marker contradicts Facebook."""
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    win_out = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, win_out, ""))

    act_out = (
        "topResumedActivity=ActivityRecord{a1b2c3 u0 com.facebook.katana/.FeedActivity t10}\n"
        "mResumedActivity: ActivityRecord{d4e5f6 u0 com.android.launcher3/.Launcher t11}\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, act_out, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, FB_XML_MINIMAL, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "com.android.launcher3" in err
