from pathlib import Path
import xml.etree.ElementTree as ET

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    collect_own_url_proof_chain,
    find_own_profile_controls,
    get_center,
    visible_tap_point,
)
from phone_runner.mobilerun import MobilerunResult

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "Agung"
TEST_ROOT_ID = "100012345678901"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "agung_own_profile_tabbar_overlap.xml"

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

PROFILE_COMPOSER_TRIGGER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""

COMPOSER_BOUND_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="Agung" content-desc="Agung" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""


def test_visible_tap_point_fixture():
    tree = ET.parse(FIXTURE_PATH)
    ok, err, more_bounds = find_own_profile_controls(tree, "Agung")
    assert ok is True
    assert more_bounds == (924, 132, 1032, 240)
    pt = visible_tap_point(tree, more_bounds)
    assert pt is not None
    x, y = pt
    assert x == 978
    assert 232 <= y <= 238


def test_visible_tap_point_no_overlap():
    xml_str = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[0,96][180,228]" content-desc="Reels, tab 1 of 6" />
    <node class="android.widget.Button" clickable="true" bounds="[924,300][1032,400]" content-desc="More profile settings" />
  </node>
</hierarchy>"""
    tree = ET.fromstring(xml_str)
    bounds = (924, 300, 1032, 400)
    pt = visible_tap_point(tree, bounds)
    assert pt == get_center(bounds)
    assert pt == (978, 350)


def test_visible_tap_point_fully_covered():
    xml_str = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[900,96][1080,238]" content-desc="Profile, tab 6 of 6" />
  </node>
</hierarchy>"""
    tree = ET.fromstring(xml_str)
    bounds = (924, 132, 1032, 240)
    pt = visible_tap_point(tree, bounds)
    assert pt is None


def _make_mock_clipboard(copied_urls):
    urls = list(copied_urls)
    current_sentinel = [None]

    def mock_clear(s, sentinel=None):
        current_sentinel[0] = sentinel
        return AdbResult(0, sentinel or "", "")

    def mock_read(s, expected_sentinel=None):
        if expected_sentinel:
            return AdbResult(0, expected_sentinel, "")
        if current_sentinel[0] and urls:
            current_sentinel[0] = None
            return AdbResult(0, urls.pop(0), "")
        return AdbResult(0, "sentinel_ready", "")

    return mock_clear, mock_read


def _make_mock_adb(adb_calls):
    def mock_adb(args, serial=None):
        cmd = list(args)
        adb_calls.append(cmd)
        if len(cmd) >= 4 and cmd[1:3] == ["ime", "set"]:
            return AdbResult(0, "OK", "")
        if len(cmd) >= 4 and cmd[1:3] == ["ime", "enable"]:
            return AdbResult(0, "OK", "")
        if len(cmd) >= 5 and cmd[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, "com.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")
        if len(cmd) >= 4 and cmd[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, "com.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")
        if len(cmd) >= 5 and cmd[1:5] == ["ime", "list", "-a", "-s"]:
            return AdbResult(0, "com.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")
        return AdbResult(0, "OK", "")
    return mock_adb


def test_settings_tap_visible_retry_flow(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(tmp_path / "obs"))
    adb_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", _make_mock_adb(adb_calls))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: MobilerunResult(0, "Navigated", ""))
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)

    probe_info = {
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
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(probe_info))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_window_state",
        lambda s: AdbResult(
            0,
            "mCurrentRotation=ROTATION_0\n"
            "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
            "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}",
            "",
        ),
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    sentinel_holder = [None]

    def mock_clear_clip(serial, sentinel=None):
        sentinel_holder[0] = sentinel
        return AdbResult(0, sentinel or "", "")

    def mock_read_clip(serial, expected_sentinel=None):
        if expected_sentinel:
            return AdbResult(0, expected_sentinel, "")
        return AdbResult(0, f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}", "")

    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear_clip)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read_clip)

    agung_xml = FIXTURE_PATH.read_text(encoding="utf-8")
    agung_tree = ET.fromstring(agung_xml)
    settings_tree = ET.fromstring(ROOT_SETTINGS_XML)
    profile_composer_tree = ET.fromstring(PROFILE_COMPOSER_TRIGGER_XML)
    composer_tree = ET.fromstring(COMPOSER_BOUND_XML)

    captures = [
        (True, "", agung_tree, agung_xml),
        (True, "", agung_tree, agung_xml),
        (True, "", settings_tree, ROOT_SETTINGS_XML),
        (True, "", profile_composer_tree, PROFILE_COMPOSER_TRIGGER_XML),
        (True, "", composer_tree, COMPOSER_BOUND_XML),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    clock = [1000.0]

    def fake_sleep(s):
        clock[0] += 6.0

    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", fake_sleep)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        time_fn=lambda: clock[0],
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID

    tap_commands = [c for c in adb_calls if len(c) >= 4 and c[:3] == ["shell", "input", "tap"]]
    settings_taps = [c for c in tap_commands if c[3] == "978"]
    assert len(settings_taps) == 2
    for tap in settings_taps:
        x = int(tap[3])
        y = int(tap[4])
        assert x == 978
        assert 232 <= y <= 238


FULLY_COVERED_PROFILE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[900,96][1080,240]" content-desc="Profile, tab 6 of 6" />
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,132][1032,240]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""


def test_settings_tap_swipe_fallback_success(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(tmp_path / "obs"))
    adb_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", _make_mock_adb(adb_calls))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: MobilerunResult(0, "Navigated", ""))
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)

    probe_info = {
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
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(probe_info))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_window_state",
        lambda s: AdbResult(
            0,
            "mCurrentRotation=ROTATION_0\n"
            "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
            "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}",
            "",
        ),
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)

    sentinel_holder = [None]

    def mock_clear_clip(serial, sentinel=None):
        sentinel_holder[0] = sentinel
        return AdbResult(0, sentinel or "", "")

    def mock_read_clip(serial, expected_sentinel=None):
        if expected_sentinel:
            return AdbResult(0, expected_sentinel, "")
        return AdbResult(0, f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}", "")

    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear_clip)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read_clip)

    covered_tree = ET.fromstring(FULLY_COVERED_PROFILE_XML)
    agung_xml = FIXTURE_PATH.read_text(encoding="utf-8")
    agung_tree = ET.fromstring(agung_xml)
    settings_tree = ET.fromstring(ROOT_SETTINGS_XML)
    profile_composer_tree = ET.fromstring(PROFILE_COMPOSER_TRIGGER_XML)
    composer_tree = ET.fromstring(COMPOSER_BOUND_XML)

    captures = [
        (True, "", covered_tree, FULLY_COVERED_PROFILE_XML),
        (True, "", agung_tree, agung_xml),
        (True, "", settings_tree, ROOT_SETTINGS_XML),
        (True, "", profile_composer_tree, PROFILE_COMPOSER_TRIGGER_XML),
        (True, "", composer_tree, COMPOSER_BOUND_XML),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    clock = [1000.0]

    def fake_sleep(s):
        clock[0] += 0.5

    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", fake_sleep)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        time_fn=lambda: clock[0],
    )

    assert proof.passed is True
    assert ["shell", "input", "swipe", "540", "1500", "540", "1300", "250"] in adb_calls


def test_settings_tap_swipe_fallback_still_covered_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(tmp_path / "obs"))
    adb_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", _make_mock_adb(adb_calls))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: MobilerunResult(0, "Navigated", ""))
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)

    probe_info = {
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
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(probe_info))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)

    covered_tree = ET.fromstring(FULLY_COVERED_PROFILE_XML)
    captures = [
        (True, "", covered_tree, FULLY_COVERED_PROFILE_XML),
        (True, "", covered_tree, FULLY_COVERED_PROFILE_XML),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "(control covered by the tab bar)" in (proof.error or "")
    assert ["shell", "input", "swipe", "540", "1500", "540", "1300", "250"] in adb_calls


PAGE_PROFILE_TABBAR_OVERLAP_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[900,96][1080,228]" content-desc="Profile, tab 6 of 6" />
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="MyPage" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,132][1032,240]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""

PAGE_SETTINGS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[361,134][719,193]" text="Page Settings" content-desc="Page Settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,367][1080,511]" text="" content-desc="Show your profile is verified" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1797][1080,1941]" text="" content-desc="Review posts and tags" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1941][1080,2085]" text="" content-desc="Search" package="com.facebook.katana" />
    <node class="android.view.ViewGroup" bounds="[36,2132][1044,2203]" text="Sharing" content-desc="Sharing" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,2224][1080,2276]" text="" content-desc="Your Page link" package="com.facebook.katana" />
  </node>
</hierarchy>"""

SWITCHER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" clickable="true" selected="true" bounds="[0,500][1080,650]" package="com.facebook.katana">
      <node text="Agung" content-desc="Agung profile" package="com.facebook.katana" />
    </node>
    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,650][1080,800]" package="com.facebook.katana">
      <node text="MyPage" content-desc="MyPage" package="com.facebook.katana" />
      <node text="Page" content-desc="Page" package="com.facebook.katana" />
    </node>
  </node>
</hierarchy>"""

PAGE_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="MyPage" content-desc="MyPage" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""


def test_page_settings_tap_visible(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(tmp_path / "obs"))
    adb_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", _make_mock_adb(adb_calls))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: MobilerunResult(0, "Navigated", ""))
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)

    probe_info = {
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
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(probe_info))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_window_state",
        lambda s: AdbResult(
            0,
            "mCurrentRotation=ROTATION_0\n"
            "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
            "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}",
            "",
        ),
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)

    mock_clear_clip, mock_read_clip = _make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        "https://www.facebook.com/profile.php?id=100098765432109",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear_clip)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read_clip)

    root_tree = ET.fromstring(PROFILE_COMPOSER_TRIGGER_XML)
    settings_tree = ET.fromstring(ROOT_SETTINGS_XML)
    switcher_tree = ET.fromstring(SWITCHER_XML)
    page_tree = ET.fromstring(PAGE_PROFILE_TABBAR_OVERLAP_XML)
    page_settings_tree = ET.fromstring(PAGE_SETTINGS_XML)
    page_composer_tree = ET.fromstring(PAGE_COMPOSER_XML)

    captures = [
        (True, "", root_tree, PROFILE_COMPOSER_TRIGGER_XML),
        (True, "", settings_tree, ROOT_SETTINGS_XML),
        (True, "", switcher_tree, SWITCHER_XML),
        (True, "", page_tree, PAGE_PROFILE_TABBAR_OVERLAP_XML),
        (True, "", page_settings_tree, PAGE_SETTINGS_XML),
        (True, "", page_tree, PAGE_PROFILE_TABBAR_OVERLAP_XML),
        (True, "", page_composer_tree, PAGE_COMPOSER_XML),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name="MyPage",
        expected_target_id="100098765432109",
    )

    assert proof.passed is True
    assert proof.observed_target_id == "100098765432109"

    tap_commands = [c for c in adb_calls if len(c) >= 4 and c[:3] == ["shell", "input", "tap"]]
    page_taps = [c for c in tap_commands if c[3] == "978" and c[4] == "236"]
    assert len(page_taps) >= 1


def test_page_settings_tap_swipe_fallback_still_covered_fails(monkeypatch, tmp_path):
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(tmp_path / "obs"))
    adb_calls = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", _make_mock_adb(adb_calls))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: MobilerunResult(0, "Navigated", ""))
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)

    probe_info = {
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
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(probe_info))
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_window_state",
        lambda s: AdbResult(0, "mCurrentRotation=ROTATION_0", ""),
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)

    mock_clear_clip, mock_read_clip = _make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear_clip)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read_clip)

    root_tree = ET.fromstring(PROFILE_COMPOSER_TRIGGER_XML)
    settings_tree = ET.fromstring(ROOT_SETTINGS_XML)
    switcher_tree = ET.fromstring(SWITCHER_XML)

    page_covered_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[900,96][1080,240]" content-desc="Profile, tab 6 of 6" />
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="MyPage" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,132][1032,240]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""
    page_covered_tree = ET.fromstring(page_covered_xml)

    captures = [
        (True, "", root_tree, PROFILE_COMPOSER_TRIGGER_XML),
        (True, "", settings_tree, ROOT_SETTINGS_XML),
        (True, "", switcher_tree, SWITCHER_XML),
        (True, "", page_covered_tree, page_covered_xml),
        (True, "", page_covered_tree, page_covered_xml),
    ]
    cap_iter = iter(captures)
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: next(cap_iter),
    )

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name="MyPage",
        expected_target_id="100098765432109",
    )

    assert proof.passed is False
    assert "(control covered by the tab bar)" in (proof.error or "")
    assert ["shell", "input", "swipe", "540", "1500", "540", "1300", "250"] in adb_calls

