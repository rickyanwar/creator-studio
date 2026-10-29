"""Tests for profile-identity-lite: name-based proof as primary path for PROFILE targets."""
import os
from pathlib import Path
import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    collect_own_url_proof_chain,
    find_root_settings_controls,
    parse_bounds,
)
from phone_runner.mobilerun import MobilerunResult

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100012345678901"
TEST_PAGE_ID = "100098765432109"

# Own profile with Edit profile + More profile settings + Dashboard + name.
# find_own_profile_controls SUCCEEDS on this tree.
OWN_PROFILE_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""

# Composer bound to Agung
COMPOSER_AGUNG_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="Agung" content-desc="Agung" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""

# Composer bound to wrong name
COMPOSER_WRONG_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="SomeoneElse" content-desc="SomeoneElse" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""

# Own profile with name mismatch
OWN_PROFILE_WRONG_NAME_XML = OWN_PROFILE_XML.replace('text="Agung"', 'text="Agung Two"')

# Visitor profile (has Add friend, no Edit profile)
VISITOR_PROFILE_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[100,300][300,400]" text="Add friend" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[400,300][600,400]" text="Message" content-desc="" package="com.facebook.katana" />
  </node>
</hierarchy>"""

# Page profile with More profile settings for page targets
PAGE_PROFILE_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="MyPage" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1418][664,1526]" text="" content-desc="Professional dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1700][1080,1850]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""

ROOT_SETTINGS_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
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

SWITCHER_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
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

PAGE_SETTINGS_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[380,134][701,193]" text="Page Settings" content-desc="Page Settings" package="com.facebook.katana" />
    <node class="android.widget.FrameLayout" clickable="true" bounds="[948,96][1068,228]" text="" content-desc="Profile switcher, Double tap to open a list of your profiles" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,367][1080,511]" text="" content-desc="Verify your Page" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,938][1080,1082]" text="" content-desc="Admins" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1509][1080,1653]" text="" content-desc="Activity log" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1941][1080,2085]" text="" content-desc="Search this Page" package="com.facebook.katana" />
    <node class="android.view.ViewGroup" bounds="[36,2132][1044,2203]" text="Sharing" content-desc="Sharing" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,2224][1080,2276]" text="" content-desc="Your Page link" package="com.facebook.katana" />
  </node>
</hierarchy>"""

PAGE_COMPOSER_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="MyPage" content-desc="MyPage" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""


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


class AdbCommandRecorder:
    def __init__(self):
        self.commands: list[list[str]] = []
        self.current_ime: str = "com.google.android.inputmethod.latin/.LatinIME\n"
        self.enabled_imes: set[str] = {"com.google.android.inputmethod.latin/.LatinIME"}

    def __call__(self, args: list[str], serial: str | None = None) -> AdbResult:
        self.commands.append(list(args))
        cmd_str = " ".join(args).lower()

        assert "input text" not in cmd_str, f"Disallowed text input in adb command: {args}"
        assert "publish" not in cmd_str, f"Disallowed publish in adb command: {args}"
        assert "next" not in cmd_str, f"Disallowed Next tap in adb command: {args}"
        assert "media" not in cmd_str, f"Disallowed media in adb command: {args}"

        allowed_prefixes = [
            ["shell", "settings", "get", "secure", "default_input_method"],
            ["shell", "ime", "list", "-s"],
            ["shell", "ime", "list", "-a", "-s"],
            ["shell", "ime", "enable"],
            ["shell", "ime", "disable"],
            ["shell", "ime", "set"],
            ["shell", "input", "tap"],
            ["shell", "dumpsys", "window", "displays"],
            ["shell", "input", "keyevent"],
            ["shell", "input", "swipe"],
            ["shell", "am", "start"],
        ]
        is_allowed = any(args[:len(p)] == p for p in allowed_prefixes)
        if not is_allowed:
            raise AssertionError(f"Unexpected adb command invoked: {args}")

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

        return AdbResult(0, "OK", "")


def make_mock_clipboard(copied_urls):
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


@pytest.fixture
def mock_device_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    default_window_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.facebook.katana/...}"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, default_window_dump, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.dry_run.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.put_to_sleep", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.dry_run.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", AdbCommandRecorder())
    monkeypatch.setattr("phone_runner.adb.run_adb", AdbCommandRecorder())

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


# ---------------------------------------------------------------------------
# Test (a): PROFILE target, own profile where find_own_profile_controls
# SUCCEEDS — result passed=True, method=own_profile_name_v1, no Settings tap,
# no clipboard, no swipe/scroll before composer.
# ---------------------------------------------------------------------------
def test_profile_target_name_proof_primary_path(mock_device_env, monkeypatch):
    """PROFILE target uses name proof as PRIMARY even when More profile settings exists."""
    ui_dumps = [
        OWN_PROFILE_XML,   # Step 2 root profile screen
        OWN_PROFILE_XML,   # Step 2 after fb://profile/<id> deep link (owner view)
        COMPOSER_AGUNG_XML, # Step 5/6 composer
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.adb.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=TEST_ROOT_ID,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is True
    assert proof.method == "own_profile_name_v1"
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_ROOT_ID
    assert any(s["step"] == "root_profile_id_bound" for s in proof.steps)

    # Exactly one fb://profile/<id> deep link was sent
    id_deep_links = [
        cmd for cmd in adb_recorder.commands
        if cmd[1:3] == ["am", "start"] and f"fb://profile/{TEST_ROOT_ID}" in cmd
    ]
    assert len(id_deep_links) == 1, f"Expected 1 id deep link, got {len(id_deep_links)}"

    # No tap on "More profile settings" bounds center (978, 320)
    more_settings_center = ("978", "320")
    for cmd in adb_recorder.commands:
        if cmd[1:3] == ["input", "tap"]:
            assert not (cmd[3] == more_settings_center[0] and cmd[4] == more_settings_center[1]), \
                f"Settings tap should not happen for profile targets: {cmd}"

    # No clipboard clear/read happened
    for cmd in adb_recorder.commands:
        cmd_joined = " ".join(cmd).lower()
        assert "clipboard" not in cmd_joined, f"Clipboard operation in profile path: {cmd}"

    # No swipe/scroll before composer step
    composer_tap_idx = None
    for i, cmd in enumerate(adb_recorder.commands):
        if cmd[1:3] == ["input", "tap"]:
            # Composer trigger center is (540, 1975) for [0,1900][1080,2050]
            if cmd[3] == "540" and cmd[4] == "1975":
                composer_tap_idx = i
                break
    for i, cmd in enumerate(adb_recorder.commands):
        if composer_tap_idx is not None and i < composer_tap_idx:
            assert cmd[1:3] != ["input", "swipe"], f"Swipe before composer: {cmd}"

    # Verify stage details
    root_stage = next(s for s in proof.stages if s["stage"] == "root_url")
    assert "name-based owner-profile proof" in root_stage["detail"]
    assert "Settings link route skipped" in root_stage["detail"]


# ---------------------------------------------------------------------------
# Test (b): PROFILE target, own profile with wrong name — fails closed.
# ---------------------------------------------------------------------------
def test_profile_target_name_mismatch_fails_closed(mock_device_env, monkeypatch):
    """PROFILE target with exact name mismatch fails closed, no composer opened."""
    ui_dumps = [OWN_PROFILE_WRONG_NAME_XML]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=TEST_ROOT_ID,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is False
    # No composer opened: no "New post" taps
    for cmd in adb_recorder.commands:
        cmd_joined = " ".join(cmd).lower()
        assert "composeractivit" not in cmd_joined


# ---------------------------------------------------------------------------
# Test (c): PROFILE target, visitor profile — fails closed.
# ---------------------------------------------------------------------------
def test_profile_target_visitor_profile_fails_closed(mock_device_env, monkeypatch):
    """Visitor profile (Add friend/Message, no Edit profile) fails closed."""
    ui_dumps = [VISITOR_PROFILE_XML]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=TEST_ROOT_ID,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is False


# ---------------------------------------------------------------------------
# Test (d): PROFILE target, name proof passes but composer author check fails.
# ---------------------------------------------------------------------------
def test_profile_target_composer_binding_fail_fails_closed(mock_device_env, monkeypatch):
    """Name proof passes but composer author mismatch → overall fails closed."""
    ui_dumps = [
        OWN_PROFILE_XML,
        OWN_PROFILE_XML,
        COMPOSER_WRONG_XML,  # composer bound to wrong name
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=TEST_ROOT_ID,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is False
    assert "composer" in (proof.error or "").lower() or "Composer" in (proof.error or "")


# ---------------------------------------------------------------------------
# Test (e): PAGE target — still goes through Settings / Copy-link URL chain.
# ---------------------------------------------------------------------------
def test_page_target_still_uses_settings_chain(mock_device_env, monkeypatch):
    """PAGE target still taps More profile settings and uses the URL chain."""
    ui_dumps = [
        OWN_PROFILE_XML,     # root profile
        ROOT_SETTINGS_XML,   # root settings
        SWITCHER_XML,        # switcher
        PAGE_PROFILE_XML,    # page profile
        PAGE_SETTINGS_XML,   # page settings
        PAGE_PROFILE_XML,    # return to page profile
        PAGE_COMPOSER_XML,   # page composer
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        f"https://www.facebook.com/profile.php?id={TEST_PAGE_ID}",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.adb.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=TEST_ROOT_ID,
        target_name="MyPage",
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is True
    assert proof.method == "own_url_chain_v1"

    # Assert More profile settings was tapped (center of [924,266][1032,374] = 978, 320)
    tap_cmds = [cmd for cmd in adb_recorder.commands if cmd[1:3] == ["input", "tap"]]
    more_settings_tapped = any(
        cmd[3] == "978" and cmd[4] == "320" for cmd in tap_cmds
    )
    assert more_settings_tapped, "Page target must tap More profile settings"


# ---------------------------------------------------------------------------
# Test (f): find_root_settings_controls on the real 2026-10-29 dump.
# ---------------------------------------------------------------------------
def test_find_root_settings_controls_new_copy_link_format():
    """find_root_settings_controls recognises the new 'Copy Agung's link' button."""
    fixture_path = Path(__file__).parent / "fixtures" / "profile_settings_copy_named_link_2026_10_29.xml"
    raw_xml = fixture_path.read_text(encoding="utf-8")
    tree = ET.fromstring(raw_xml)

    ok, err, switcher_bounds, copy_link_bounds = find_root_settings_controls(tree)
    assert ok is True, f"Expected ok=True, got err={err}"
    assert copy_link_bounds is not None, "Expected copy_link_bounds to be set"
    # bounds of content-desc="Copy Agung's link" is [48,2120][1032,2228]
    assert copy_link_bounds == (48, 2120, 1032, 2228)


# ---------------------------------------------------------------------------
# Test (g): PROFILE target with profile_url_proof=True forces URL chain.
# ---------------------------------------------------------------------------
def test_profile_url_proof_flag_forces_settings_chain(mock_device_env, monkeypatch):
    """profile_url_proof=True forces the Settings / Copy-link URL chain for a profile target."""
    ui_dumps = [
        OWN_PROFILE_XML,     # Step 2 root profile screen
        ROOT_SETTINGS_XML,   # Settings screen after tap
        OWN_PROFILE_XML,     # back on profile screen
        COMPOSER_AGUNG_XML,  # composer
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

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=TEST_ROOT_ID,
        target_name=None,
        expected_target_id=None,
        profile_url_proof=True,
    )

    assert proof.passed is True
    assert proof.method == "own_url_chain_v1"

    # Assert More profile settings was tapped (center of [924,266][1032,374] = 978, 320)
    tap_cmds = [cmd for cmd in adb_recorder.commands if cmd[1:3] == ["input", "tap"]]
    more_settings_tapped = any(
        cmd[3] == "978" and cmd[4] == "320" for cmd in tap_cmds
    )
    assert more_settings_tapped, "profile_url_proof=True must tap More profile settings"


# ---------------------------------------------------------------------------
# Test: 2.a profile id binding owner view passes
# ---------------------------------------------------------------------------
def test_profile_id_binding_owner_view_passes(mock_device_env, monkeypatch):
    """Profile target with matching numeric root id binds via deep link and owner view passes."""
    expected_id = "100009439229483"
    ui_dumps = [
        OWN_PROFILE_XML,
        OWN_PROFILE_XML,
        COMPOSER_AGUNG_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.adb.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=expected_id,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is True
    assert proof.method == "own_profile_name_v1"
    assert any(s["step"] == "root_profile_id_bound" for s in proof.steps)

    expected_deep_link = [
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        f"fb://profile/{expected_id}",
        "com.facebook.katana",
    ]
    matching_starts = [cmd for cmd in adb_recorder.commands if cmd == expected_deep_link]
    assert len(matching_starts) == 1, f"Expected exactly one id deep link {expected_deep_link}, got: {matching_starts}"


# ---------------------------------------------------------------------------
# Test: 2.b profile id binding visitor view fails closed
# ---------------------------------------------------------------------------
def test_profile_id_binding_visitor_view_fails_closed(mock_device_env, monkeypatch):
    """Profile target where id deep link shows visitor view fails closed without composer."""
    expected_id = "100009439229483"
    ui_dumps = [
        OWN_PROFILE_XML,
        VISITOR_PROFILE_XML,
        VISITOR_PROFILE_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.adb.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=expected_id,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is False
    assert "Profile id binding failed" in (proof.error or "")
    # Composer is NEVER opened
    for cmd in adb_recorder.commands:
        if cmd[1:3] == ["input", "tap"]:
            assert not (cmd[3] == "540" and cmd[4] == "1975"), f"Composer trigger was tapped: {cmd}"


# ---------------------------------------------------------------------------
# Test: 2.c profile id binding non-numeric id fails closed
# ---------------------------------------------------------------------------
def test_profile_id_binding_non_numeric_id_fails_closed(mock_device_env, monkeypatch):
    """Expected root id with non-digits fails closed immediately without deep link."""
    ui_dumps = [
        OWN_PROFILE_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.adb.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id="agung.vanity",
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is False
    assert "Profile id binding failed" in (proof.error or "")
    # No deep link sent
    for cmd in adb_recorder.commands:
        cmd_joined = " ".join(cmd)
        assert "fb://profile/" not in cmd_joined, f"Deep link sent for non-numeric id: {cmd}"


# ---------------------------------------------------------------------------
# Test: 2.d profile id binding switcher sheet closed with back
# ---------------------------------------------------------------------------
SWITCHER_SHEET_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[0,0][1080,100]" text="Dismiss" content-desc="Dismiss" package="com.facebook.katana" />
    <node class="android.view.ViewGroup" clickable="true" bounds="[0,100][1080,300]" text="Page" content-desc="Page" package="com.facebook.katana" />
  </node>
</hierarchy>"""


def test_profile_id_binding_switcher_sheet_closed_with_back(mock_device_env, monkeypatch):
    """Switcher sheet after deep link is dismissed with ONE Back keyevent, then owner view passes."""
    expected_id = "100009439229483"
    ui_dumps = [
        OWN_PROFILE_XML,      # Step 2 initial check
        SWITCHER_SHEET_XML,   # First dump after deep link: switcher sheet open
        OWN_PROFILE_XML,      # Dump after Back keyevent: owner view
        COMPOSER_AGUNG_XML,   # Composer
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.adb.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Agung",
        expected_root_id=expected_id,
        target_name=None,
        expected_target_id=None,
    )

    assert proof.passed is True
    assert proof.method == "own_profile_name_v1"
    assert any(s["step"] == "root_profile_id_bound" for s in proof.steps)

    # Fake adb received Back keyevent (keycode 4)
    back_cmds = [cmd for cmd in adb_recorder.commands if cmd[1:] == ["input", "keyevent", "4"]]
    assert len(back_cmds) == 1, f"Expected 1 Back keyevent, got: {back_cmds}"


# ---------------------------------------------------------------------------
# Test: 2.e launch_facebook_profile_by_id unit tests
# ---------------------------------------------------------------------------
def test_launch_facebook_profile_by_id():
    """launch_facebook_profile_by_id validates numeric id and issues exact am start command."""
    from phone_runner.adb import launch_facebook_profile_by_id

    # Non-digit / invalid ids raise ValueError
    with pytest.raises(ValueError, match="profile_id must be numeric"):
        launch_facebook_profile_by_id(TEST_SERIAL, "agung.vanity")

    with pytest.raises(ValueError, match="profile_id must be numeric"):
        launch_facebook_profile_by_id(TEST_SERIAL, "123")  # too short (< 4 digits)

    with pytest.raises(ValueError, match="profile_id must be numeric"):
        launch_facebook_profile_by_id(TEST_SERIAL, "123456789012345678901234567890123")  # too long (> 32 digits)

    # Valid digit id builds exact command
    recorded = []

    def fake_run(args, serial=None):
        recorded.append((args, serial))
        return AdbResult(0, "OK", "")

    from unittest.mock import patch
    with patch("phone_runner.adb.run_adb", side_effect=fake_run):
        res = launch_facebook_profile_by_id(TEST_SERIAL, "100009439229483")

    assert res.returncode == 0
    assert len(recorded) == 1
    args, serial = recorded[0]
    assert serial == TEST_SERIAL
    assert args == [
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        "fb://profile/100009439229483",
        "com.facebook.katana",
    ]


