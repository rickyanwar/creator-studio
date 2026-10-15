"""Tests for deterministic root navigation and measured two-editor own profile."""

import re
import xml.etree.ElementTree as ET
from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.dry_run import execute_dry_run
from phone_runner.identity_proof import (
    _find_immersive_profile_controls,
    _try_open_own_root_without_model,
    collect_own_url_proof_chain,
    find_go_to_profile_button,
    find_own_profile_controls,
)
from phone_runner.mobilerun import MobilerunResult


TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "DyQ User"
TEST_ROOT_ID = "100088991122"
TEST_TARGET_PAGE_NAME = "DyQ Page"
TEST_TARGET_PAGE_ID = "200088991122"


def _make_two_editor_dyq_profile_xml(name: str = TEST_ROOT_NAME) -> str:
    """DyQ measured layout: 2 editors (cover photo + profile picture), NO Edit personal details."""
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[918,96][1080,228]" content-desc="More profile settings" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[936,522][1068,654]" content-desc="Edit cover photo" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[654,685][762,793]" content-desc="Edit profile picture" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[396,854][606,936]" content-desc="" text="{name}" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[36,1713][530,1821]" content-desc="Dashboard" text="Dashboard" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" />
</hierarchy>"""


def _make_three_editor_agung_profile_xml(name: str = TEST_ROOT_NAME) -> str:
    """Agung measured layout: 3 editors (cover photo + profile picture + Personal details)."""
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[918,96][1080,228]" content-desc="More profile settings" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[936,522][1068,654]" content-desc="Edit cover photo" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[654,685][762,793]" content-desc="Edit profile picture" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[396,854][606,936]" content-desc="" text="{name}" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[984,1884][1044,1947]" content-desc="Edit Personal details" text="" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[36,1713][530,1821]" content-desc="Dashboard" text="Dashboard" />
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" />
</hierarchy>"""


def _make_feed_with_go_to_profile_xml(text_or_desc: str = "Go to profile") -> str:
    return f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0" bounds="[0,0][1080,2408]">
  <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[800,150][940,210]" content-desc="{text_or_desc}" text="{text_or_desc}" />
  <node package="com.facebook.katana" class="android.widget.TextView" text="News Feed" />
</hierarchy>"""


ROOT_SETTINGS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[361,134][719,193]" text="Profile Settings" content-desc="Profile Settings" package="com.facebook.katana" />
    <node class="android.widget.FrameLayout" clickable="true" bounds="[948,96][1068,228]" text="" content-desc="Profile switcher, Double tap to open a list of your profiles" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,367][1080,511]" text="" content-desc="Show your profile is verified" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1797][1080,1941]" text="" content-desc="Review posts and tags" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1941][1080,2085]" text="" content-desc="Search" package="com.facebook.katana" />
    <node class="android.view.ViewGroup" bounds="[36,2132][1044,2203]" text="Sharing" content-desc="Sharing" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,2224][1080,2276]" text="" content-desc="Copy profile link" package="com.facebook.katana" />
  </node>
</hierarchy>"""

PAGE_COMPOSER_XML = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="{TEST_ROOT_NAME}" content-desc="{TEST_ROOT_NAME}" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""


def test_two_editor_and_three_editor_owned_variants():
    # 2-editor DyQ layout passes
    tree2 = ET.fromstring(_make_two_editor_dyq_profile_xml())
    ok, err, bounds = find_own_profile_controls(tree2, TEST_ROOT_NAME)
    assert ok is True
    assert err == ""
    assert bounds == (918, 96, 1080, 228)

    # 3-editor Agung layout passes
    tree3 = ET.fromstring(_make_three_editor_agung_profile_xml())
    ok, err, bounds = find_own_profile_controls(tree3, TEST_ROOT_NAME)
    assert ok is True
    assert err == ""
    assert bounds == (918, 96, 1080, 228)

    # Missing cover photo rejects
    xml_no_cover = _make_two_editor_dyq_profile_xml().replace('content-desc="Edit cover photo"', 'content-desc="Cover"')
    ok, err, _ = find_own_profile_controls(ET.fromstring(xml_no_cover), TEST_ROOT_NAME)
    assert ok is False

    # Missing profile picture rejects
    xml_no_pic = _make_two_editor_dyq_profile_xml().replace('content-desc="Edit profile picture"', 'content-desc="Pic"')
    ok, err, _ = find_own_profile_controls(ET.fromstring(xml_no_pic), TEST_ROOT_NAME)
    assert ok is False

    # Present duplicate Personal details rejects
    dup_personal_xml = _make_three_editor_agung_profile_xml().replace(
        '</hierarchy>',
        '<node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[984,2000][1044,2060]" content-desc="Edit Personal details" text="" /></hierarchy>'
    )
    ok, err, _ = find_own_profile_controls(ET.fromstring(dup_personal_xml), TEST_ROOT_NAME)
    assert ok is False

    # Present unclickable / disabled Personal details rejects
    disabled_personal_xml = _make_three_editor_agung_profile_xml().replace(
        'clickable="true" enabled="true" bounds="[984,1884][1044,1947]" content-desc="Edit Personal details"',
        'clickable="false" enabled="true" bounds="[984,1884][1044,1947]" content-desc="Edit Personal details"'
    )
    ok, err, _ = find_own_profile_controls(ET.fromstring(disabled_personal_xml), TEST_ROOT_NAME)
    assert ok is False


def test_tight_find_go_to_profile_button_exact_equality():
    # Exact match passes
    exact_tree = ET.fromstring(_make_feed_with_go_to_profile_xml("Go to profile"))
    ok, err, bounds = find_go_to_profile_button(exact_tree)
    assert ok is True
    assert bounds == (800, 150, 940, 210)

    # Substring match rejected
    sub_tree = ET.fromstring(_make_feed_with_go_to_profile_xml("Please go to profile now"))
    ok, err, bounds = find_go_to_profile_button(sub_tree)
    assert ok is False

    # Ambiguous duplicate rejected
    dup_tree = ET.fromstring("""<hierarchy>
      <node class="android.widget.Button" clickable="true" bounds="[10,10][100,50]" text="Go to profile" />
      <node class="android.widget.Button" clickable="true" bounds="[200,200][300,250]" text="Go to profile" />
    </hierarchy>""")
    ok, err, bounds = find_go_to_profile_button(dup_tree)
    assert ok is False
    assert "Ambiguous" in err


def test_real_helper_already_owned_root_returns_true_zero_mobilerun_no_actions(monkeypatch):
    adb_calls = []

    def fake_adb(args, serial=None):
        cmd = " ".join(args)
        adb_calls.append(cmd)
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake_adb)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (True, "", ET.fromstring(_make_two_editor_dyq_profile_xml()), _make_two_editor_dyq_profile_xml()),
    )
    mob_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: mob_calls.append(kw))

    res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)
    assert res is True
    assert len(mob_calls) == 0
    # No tap actions when already on own root
    tap_calls = [c for c in adb_calls if "input tap" in c]
    assert len(tap_calls) == 0


def test_real_helper_exact_go_to_profile_one_tap_then_fresh_own_root_returns_true(monkeypatch):
    adb_calls = []

    def fake_adb(args, serial=None):
        cmd = " ".join(args)
        adb_calls.append(cmd)
        return AdbResult(0, "OK", "")

    captures = [
        (True, "", ET.fromstring(_make_feed_with_go_to_profile_xml()), _make_feed_with_go_to_profile_xml()),
        (True, "", ET.fromstring(_make_two_editor_dyq_profile_xml()), _make_two_editor_dyq_profile_xml()),
    ]
    cap_iter = iter(captures)

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake_adb)
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(cap_iter))
    mob_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: mob_calls.append(kw))

    res = _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME)
    assert res is True
    assert len(mob_calls) == 0
    # Exactly one tap at center of [800,150][940,210] -> (870, 180)
    tap_calls = [c for c in adb_calls if "input tap" in c]
    assert len(tap_calls) == 1
    assert "870 180" in tap_calls[0]


def test_real_helper_ambiguous_substring_foreign_public_failedcapture_returns_false(monkeypatch):
    adb_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (adb_calls.append(" ".join(args)), AdbResult(0, "OK", ""))[1])

    # 1. Failed capture
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (False, "UI dump failed", None, ""),
    )
    assert _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME) is False

    # 2. Substring Go to profile
    sub_xml = _make_feed_with_go_to_profile_xml("Tap here to go to profile")
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (True, "", ET.fromstring(sub_xml), sub_xml),
    )
    assert _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME) is False

    # 3. Foreign name on profile
    foreign_xml = _make_two_editor_dyq_profile_xml(name="Foreign Name")
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (True, "", ET.fromstring(foreign_xml), foreign_xml),
    )
    assert _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME) is False

    # 4. Public profile with visitor action
    pub_xml = _make_two_editor_dyq_profile_xml().replace(
        '</hierarchy>',
        '<node package="com.facebook.katana" class="android.widget.Button" clickable="true" enabled="true" bounds="[100,1200][300,1300]" content-desc="Add Friend" text="Add Friend" /></hierarchy>'
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (True, "", ET.fromstring(pub_xml), pub_xml),
    )
    assert _try_open_own_root_without_model(TEST_SERIAL, TEST_ROOT_NAME) is False

    # No taps made in any of these rejection paths
    assert len(adb_calls) == 0


class AdbCommandRecorder:
    """Validating recorder for adb commands that simulates realistic IME behavior."""

    def __init__(self):
        self.commands: list[list[str]] = []
        self.current_ime: str = "com.google.android.inputmethod.latin/.LatinIME\n"
        self.enabled_imes: set[str] = {"com.google.android.inputmethod.latin/.LatinIME"}

    def __call__(self, args: list[str], serial: str | None = None) -> AdbResult:
        self.commands.append(list(args))
        if len(args) >= 4 and args[1:3] == ["ime", "set"]:
            self.current_ime = args[3].strip() + "\n"
            return AdbResult(0, "OK", "")
        if len(args) >= 4 and args[1:3] == ["ime", "enable"]:
            self.enabled_imes.add(args[3].strip())
            return AdbResult(0, "OK", "")
        if len(args) >= 4 and args[1:3] == ["ime", "disable"]:
            self.enabled_imes.discard(args[3].strip())
            return AdbResult(0, "OK", "")

        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, self.current_ime, "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, "\n".join(self.enabled_imes) + "\n", "")
        if len(args) >= 5 and args[1:5] == ["ime", "list", "-a", "-s"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\ncom.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-a"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\ncom.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")

        return AdbResult(0, "OK", "")


def test_collector_candidate_ready_skips_mobilerun_and_passes_full_chain(monkeypatch):
    """Real helper finds candidate own root immediately -> skips Mobilerun, passes Step 2 and all gates."""
    ui_dumps = [
        _make_two_editor_dyq_profile_xml(),  # helper fresh check
        _make_two_editor_dyq_profile_xml(),  # Step 2 fresh own profile check
        ROOT_SETTINGS_XML,                   # Step 3 root settings check
        _make_two_editor_dyq_profile_xml(),  # return to root profile
        PAGE_COMPOSER_XML,                   # composer readiness check
    ]
    ui_iter = iter(ui_dumps)

    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_activity_state",
        lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_window_state",
        lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{123 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana", ""),
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-15T12:00:00Z",
    })

    copied_urls = [f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}"]
    current_sentinel = [None]

    def mock_clear(s, sentinel=None):
        current_sentinel[0] = sentinel
        return AdbResult(0, sentinel or "", "")

    def mock_read(s, expected_sentinel=None):
        if expected_sentinel:
            return AdbResult(0, expected_sentinel, "")
        if current_sentinel[0] and copied_urls:
            current_sentinel[0] = None
            return AdbResult(0, copied_urls.pop(0), "")
        return AdbResult(0, "sentinel_ready", "")

    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_rec = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_rec)

    mob_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: (mob_calls.append(kw), MobilerunResult(0, "OK", ""))[1])

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID
    # ZERO mobilerun calls made!
    assert len(mob_calls) == 0


def test_collector_wrong_numeric_ids_still_reject(monkeypatch):
    """Even if candidate-ready without model, wrong numeric URL rejects proof chain."""
    ui_dumps = [
        _make_two_editor_dyq_profile_xml(),  # helper check
        _make_two_editor_dyq_profile_xml(),  # Step 2 check
        ROOT_SETTINGS_XML,                   # Step 3 settings
    ]
    ui_iter = iter(ui_dumps)

    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{123 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-15T12:00:00Z",
    })

    # Return WRONG numeric ID
    copied_urls = ["https://www.facebook.com/profile.php?id=999999999999"]
    current_sentinel = [None]

    def mock_clear(s, sentinel=None):
        current_sentinel[0] = sentinel
        return AdbResult(0, sentinel or "", "")

    def mock_read(s, expected_sentinel=None):
        if expected_sentinel:
            return AdbResult(0, expected_sentinel, "")
        if current_sentinel[0] and copied_urls:
            current_sentinel[0] = None
            return AdbResult(0, copied_urls.pop(0), "")
        return AdbResult(0, "sentinel_ready", "")

    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_rec = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_rec)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "does not match expected" in (proof.error or "")


def test_collector_deadline_consumed_before_fallback_model_fails_without_mobilerun_and_restores_ime(monkeypatch):
    """When deterministic helper consumes deadline and returns False, zero mobilerun, IME restored."""
    current_time = [100.0]
    def fake_time():
        return current_time[0]

    monkeypatch.setattr("phone_runner.identity_proof.time.monotonic", fake_time)

    def fake_helper(*a, **kw):
        current_time[0] += 300.0  # Helper consumes the deadline!
        return False

    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", fake_helper)
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{123 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-15T12:00:00Z",
    })

    adb_rec = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_rec)

    mob_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: (mob_calls.append(kw), MobilerunResult(0, "OK", ""))[1])

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        timeout=240.0,
    )

    assert proof.passed is False
    assert "timeout" in proof.error.lower() or "deadline" in proof.error.lower()
    # ZERO mobilerun calls!
    assert len(mob_calls) == 0
    # Original IME restored by context manager exit
    set_latin_calls = [c for c in adb_rec.commands if len(c) >= 4 and c[1:3] == ["ime", "set"] and "latin" in c[3]]
    assert len(set_latin_calls) >= 1


def test_dry_run_modern_capture_no_direct_ids_skips_preliminary_model(monkeypatch):
    """Modern FB without direct IDs in XML/dumpsys skips preliminary run_mobilerun and calls collector directly."""
    fake_probe = {
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
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: dict(fake_probe))
    monkeypatch.setattr("phone_runner.dry_run.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))

    # Modern capture: FeedActivity without c_user, hierarchy without composer_actor
    monkeypatch.setattr(
        "phone_runner.dry_run.query_ui_hierarchy",
        lambda s: AdbResult(0, "<hierarchy><node class='android.widget.TextView' text='Feed' /></hierarchy>", ""),
    )
    monkeypatch.setattr(
        "phone_runner.dry_run.query_activity_state",
        lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""),
    )

    mob_calls = []
    monkeypatch.setattr("phone_runner.dry_run.run_mobilerun", lambda **kw: (mob_calls.append(kw), MobilerunResult(0, "OK", ""))[1])

    collector_called = []
    from phone_runner.identity_proof import IdentityProofResult

    fake_proof = IdentityProofResult(
        nonce="test_nonce_modern",
        created_at="2026-10-15T12:00:00Z",
        serial=TEST_SERIAL,
        method="own_url_chain_v1",
        evidence_source="adb_device_read",
        observed_root_id=TEST_ROOT_ID,
        observed_root_url=f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        observed_target_id=TEST_ROOT_ID,
        observed_target_url=f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        verified_main_profile_id=TEST_ROOT_ID,
        active_composer_actor_id=TEST_ROOT_ID,
        app_card_version=None,
        facebook_version="580.0.0.51.74",
        portal_version="0.7.25",
        resolution="1080x2408",
        rotation=0,
        steps=[],
        passed=True,
        stages=[
            {"stage": "collector_root", "passed": True, "detail": "Verified root URL"},
            {"stage": "collector_composer", "passed": True, "detail": "Verified composer"},
        ],
        composer_readiness={"has_header": True, "author_matched": True},
    )
    monkeypatch.setattr("phone_runner.dry_run.collect_own_url_proof_chain", lambda **kw: (collector_called.append(kw), fake_proof)[1])

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
    )

    assert res["success"] is True
    assert res["status"] == "ready"
    # Preliminary model navigation was SKIPPED!
    assert len(mob_calls) == 0
    # Real collector was called!
    assert len(collector_called) == 1
    assert collector_called[0]["serial"] == TEST_SERIAL


def test_dry_run_legacy_ids_trigger_legacy_navigation_and_strict_gates(monkeypatch):
    """Presence of legacy direct ID in capture triggers legacy run_mobilerun, recapture, and strict checks."""
    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "450.0.0.34.109",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: dict(fake_probe))
    monkeypatch.setattr("phone_runner.dry_run.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))

    legacy_ui = f"""<hierarchy rotation="0">
      <node resource-id="com.facebook.katana:id/composer_actor" content-desc="fb://profile/{TEST_ROOT_ID}" text="{TEST_ROOT_NAME}" />
      <node resource-id="com.facebook.katana:id/composer_text_input" text="What's on your mind?" />
    </hierarchy>"""
    legacy_act = f"Intent: act=android.intent.action.VIEW c_user={TEST_ROOT_ID} cmp=com.facebook.katana/.FeedActivity"

    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(0, legacy_ui, ""))
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, legacy_act, ""))

    mob_calls = []
    monkeypatch.setattr(
        "phone_runner.dry_run.run_mobilerun",
        lambda **kw: (mob_calls.append(kw), MobilerunResult(0, "Navigated to profile info and composer.", ""))[1],
    )

    collector_called = []
    monkeypatch.setattr("phone_runner.dry_run.collect_own_url_proof_chain", lambda **kw: collector_called.append(kw))

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
    )

    assert res["success"] is True
    assert res["status"] == "ready"
    assert len(mob_calls) == 1
    assert len(collector_called) == 0


def test_dry_run_failed_initial_capture_fails_before_model(monkeypatch):
    """Initial capture returning nonzero fails immediately before calling run_mobilerun."""
    fake_probe = {
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
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: dict(fake_probe))
    monkeypatch.setattr("phone_runner.dry_run.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))

    # UI capture fails
    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(1, "", "uiautomator error"))
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, "FeedActivity", ""))

    mob_calls = []
    monkeypatch.setattr("phone_runner.dry_run.run_mobilerun", lambda **kw: mob_calls.append(kw))

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
    )

    assert res["success"] is False
    assert res["status"] == "identity_unverified"
    assert "capture failed" in res["stages"][-1]["detail"]
    assert len(mob_calls) == 0


def test_dry_run_legacy_ids_deadline_consumed_fails_before_model(monkeypatch):
    """When legacy IDs are present but deadline is already consumed, fails immediately without calling run_mobilerun."""
    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "450.0.0.34.109",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: dict(fake_probe))
    monkeypatch.setattr("phone_runner.dry_run.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))

    legacy_ui = f"""<hierarchy rotation="0">
      <node resource-id="com.facebook.katana:id/composer_actor" content-desc="fb://profile/{TEST_ROOT_ID}" text="{TEST_ROOT_NAME}" />
    </hierarchy>"""
    legacy_act = f"c_user={TEST_ROOT_ID}"
    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(0, legacy_ui, ""))
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, legacy_act, ""))

    time_val = [0.0]
    def fake_time():
        time_val[0] += 300.0  # Consumes beyond default 240s deadline
        return time_val[0]

    monkeypatch.setattr("phone_runner.dry_run.time.monotonic", fake_time)

    mob_calls = []
    monkeypatch.setattr("phone_runner.dry_run.run_mobilerun", lambda **kw: mob_calls.append(kw))

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
    )

    assert res["success"] is False
    assert res["status"] == "identity_unverified"
    assert "deadline exceeded" in res["stages"][-1]["detail"].lower()
    assert len(mob_calls) == 0


def test_collector_helper_false_calls_mobilerun_and_fails_on_ime_restoration_failure(monkeypatch):
    """When helper is False, fallback mobilerun is called; if IME restoration fails, proof fails immediately."""
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{123 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-15T12:00:00Z",
    })
    adb_rec = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_rec)

    mob_calls = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.run_mobilerun",
        lambda **kw: (mob_calls.append(kw), MobilerunResult(-1, "", "IME_RESTORATION_FAILED"))[1],
    )

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "IME restoration failed" in proof.error
    assert len(mob_calls) == 1
