import io
import json
from unittest import mock
import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.dry_run import execute_dry_run, parse_device_identity_evidence
from phone_runner.identity_proof import (
    IdentityProofResult,
    PortalImeContext,
    SETTINGS_POLL_SECONDS,
    SETTINGS_WAIT_SECONDS,
    bind_portal_ime_if_needed,
    collect_own_url_proof_chain,
    find_composer_open_trigger,
    find_go_to_profile_button,
    find_own_profile_controls,
    find_own_profile_name_identity,
    find_switcher_target_page,
    verify_foreground_facebook,
)
from phone_runner.live_dry_run import run_live_dry_run
from phone_runner.mobilerun import MobilerunResult
from phone_runner.publish import execute_publish

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "OwnRootUser"
TEST_ROOT_ID = "100012345678901"
TEST_PAGE_NAME = "OwnPageTarget"
TEST_PAGE_ID = "100098765432109"

ROOT_PROFILE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="OwnRootUser" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1900][1080,2050]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
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

SWITCHER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" clickable="true" selected="true" bounds="[0,500][1080,650]" package="com.facebook.katana">
      <node text="OwnRootUser" content-desc="OwnRootUser profile" package="com.facebook.katana" />
    </node>
    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,650][1080,800]" package="com.facebook.katana">
      <node text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
      <node text="Page" content-desc="Page" package="com.facebook.katana" />
    </node>
  </node>
</hierarchy>"""

PAGE_PROFILE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="OwnPageTarget" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1418][664,1526]" text="" content-desc="Professional dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1700][1080,1850]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""

PAGE_SETTINGS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
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

PAGE_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[36,350][1044,800]" text="" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[900,120][1044,180]" text="Next" content-desc="Next" package="com.facebook.katana" />
  </node>
</hierarchy>"""

ROOT_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="OwnRootUser" content-desc="OwnRootUser" package="com.facebook.katana" />
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
    """Validating recorder for adb commands that rejects unknown actions and forbids typing/media/next/publish."""

    def __init__(self):
        self.commands: list[list[str]] = []
        self.current_ime: str = "com.google.android.inputmethod.latin/.LatinIME\n"
        self.enabled_imes: set[str] = {"com.google.android.inputmethod.latin/.LatinIME"}

    def __call__(self, args: list[str], serial: str | None = None) -> AdbResult:
        self.commands.append(list(args))
        cmd_str = " ".join(args).lower()

        # Strict safety assertions: no text entry, no media picking, no Next or publish clicks
        assert "input text" not in cmd_str, f"Disallowed text input in adb command: {args}"
        assert "publish" not in cmd_str, f"Disallowed publish in adb command: {args}"
        assert "next" not in cmd_str, f"Disallowed Next tap in adb command: {args}"
        assert "media" not in cmd_str, f"Disallowed media in adb command: {args}"

        # Allowed command prefixes in proof collection & ime handling
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
    monkeypatch.setattr("phone_runner.live_dry_run.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
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


def test_verified_root_and_page_success(mock_device_env, monkeypatch):
    """Proves full host-owned proof chain for Root Profile + Page Target."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        SWITCHER_XML,
        PAGE_PROFILE_XML,
        PAGE_SETTINGS_XML,
        PAGE_PROFILE_XML,
        PAGE_COMPOSER_XML,
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
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_PAGE_ID
    assert proof.method == "own_url_chain_v1"
    assert proof.evidence_source == "adb_device_read"
    assert len(proof.steps) >= 5
    assert proof.composer_readiness.get("has_header") is True
    assert proof.composer_readiness.get("author_matched") is True
    assert len(adb_recorder.commands) > 0


def test_profile_only_success(mock_device_env, monkeypatch):
    """Proves profile-only success without switching to a Page."""
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

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_ROOT_ID
    assert any(s["stage"] == "composer_binding" and s["passed"] for s in proof.stages)


def test_wrong_root_id_fails_before_switcher(mock_device_env, monkeypatch):
    """If observed root ID differs from expected, must fail before touching switcher."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([
        "https://www.facebook.com/profile.php?id=999999999999",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is False
    assert "does not match expected" in (proof.error or "")
    assert not any(s["stage"] == "hierarchy" for s in proof.stages)
    assert not any(s["stage"] == "target_url" for s in proof.stages)


def test_wrong_page_id_fails(mock_device_env, monkeypatch):
    """If observed target Page ID differs from expected, fails closed."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        SWITCHER_XML,
        PAGE_PROFILE_XML,
        PAGE_SETTINGS_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        "https://www.facebook.com/profile.php?id=888888888888",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is False
    assert "Observed Page ID" in (proof.error or "")
    assert not any(s["stage"] == "composer_binding" for s in proof.stages)


def test_duplicate_page_names_in_switcher_fails(mock_device_env, monkeypatch):
    """If account switcher contains duplicate rows with target name, must fail closed."""
    dup_switcher_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" selected="true" package="com.facebook.katana">
          <node text="OwnRootUser" content-desc="OwnRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" package="com.facebook.katana">
          <node text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""

    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        dup_switcher_xml,
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
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is False
    assert "Ambiguous switcher rows" in (proof.error or "")


def test_page_masquerading_as_root_fails(mock_device_env, monkeypatch):
    """If screen is Page Settings when expecting root profile, fails closed."""
    ui_dumps = [
        PAGE_SETTINGS_XML, # wrong screen at step 2
    ]
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, PAGE_SETTINGS_XML, ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is False
    assert "Root own profile invalid" in (proof.error or "")


def test_visitor_profile_fails(mock_device_env, monkeypatch):
    """Visiting a public profile without Edit profile button fails closed."""
    visitor_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node text="OwnRootUser" package="com.facebook.katana" />
        <node text="Add friend" desc="Add friend" clickable="true" package="com.facebook.katana" />
        <node text="Message" desc="Message" clickable="true" package="com.facebook.katana" />
      </node>
    </hierarchy>"""

    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, visitor_xml, ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "visitor profile" in (proof.error or "").lower()


def test_clipboard_sentinel_readback_failed_fails(mock_device_env, monkeypatch):
    """If sentinel readback returns stale or empty value, proof fails immediately."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    # Sentinel readback mismatch
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", lambda s, sentinel: AdbResult(0, sentinel, ""))
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", lambda s, expected_sentinel=None: AdbResult(0, "stale_clipboard_token", ""))
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is False
    assert "sentinel readback" in (proof.error or "").lower()


def test_failed_or_malformed_ui_capture_fails(mock_device_env, monkeypatch):
    """Capture failure or malformed XML fails closed without crashing."""
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(-1, "", "uiautomator error"))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "failed" in (proof.error or "").lower() and "capture" in (proof.error or "").lower()


def test_wrong_foreground_fails(mock_device_env, monkeypatch):
    """If top activity or package is not Facebook Katana, fails closed."""
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.android.launcher3/.Launcher", ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "not facebook katana" in (proof.error or "").lower()


def test_rotation_drift_fails(mock_device_env, monkeypatch):
    """If screen rotation is not 0 (e.g. landscape 1), fails closed."""
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, "mCurrentRotation=ROTATION_90", ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert "rotation" in (proof.error or "").lower()


def test_caption_reflection_in_legacy_extractor_ignored():
    """Editable caption containing c_user or actor ID does not fool session extraction."""
    malicious_caption_ui = """<hierarchy rotation="0">
      <node class="android.widget.EditText" text="Check this out c_user=999999999999 active_user_id=888888888888" />
    </hierarchy>"""
    activity_dump = "Intent: act=android.intent.action.VIEW cmp=com.facebook.katana/.FeedActivity"

    active_actor, main_profile, switchers = parse_device_identity_evidence(malicious_caption_ui, activity_dump)
    assert main_profile is None
    assert active_actor is None


def test_no_model_called_after_terminal_proof(mock_device_env, monkeypatch):
    """Asserts Mobilerun is invoked only for initial navigation assistance, never between URL proof and composer."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        ROOT_COMPOSER_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mobilerun_calls = []
    def recording_mobilerun(**kwargs):
        mobilerun_calls.append(kwargs)
        return MobilerunResult(0, "Navigated", "")

    mock_clear, mock_read = make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", recording_mobilerun)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is True
    # Exactly one initial navigation call
    assert len(mobilerun_calls) == 1
    assert "DRY-RUN INSPECTION ONLY" in mobilerun_calls[0]["prompt"]


def test_ime_restoration_guaranteed(mock_device_env, monkeypatch):
    """Proves original default IME is restored in finally on every success or error."""
    commands_run = []
    def mock_run_adb(args, serial=None):
        commands_run.append(args)
        if "get" in args and "default_input_method" in args:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        if "ime" in args and "list" in args and "-s" in args and "-a" not in args:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        if "ime" in args and "list" in args and "-a" in args:
            return AdbResult(0, "com.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", mock_run_adb)

    # Trigger error inside context
    try:
        with PortalImeContext(TEST_SERIAL):
            raise RuntimeError("Simulated failure inside IME context")
    except RuntimeError:
        pass

    # Verify IME set was called with original default
    restore_calls = [cmd for cmd in commands_run if cmd[:3] == ["shell", "ime", "set"] and "com.google.android.inputmethod.latin" in cmd[3]]
    assert len(restore_calls) > 0


def test_secret_or_token_not_echoed_in_error_or_report(mock_device_env, monkeypatch):
    """Secret clipboard sentinels, XML dumps, and raw errors must never appear in report."""
    private_token = "private_secret_token_abcdef123456"

    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    # Clipboard returns an unknown error string with private token
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", lambda s, sentinel: AdbResult(-1, "", f"Error with {private_token}"))
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", lambda s, expected_sentinel=None: AdbResult(-1, "", f"Error with {private_token}"))
    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    report = run_live_dry_run(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        main_id=TEST_ROOT_ID,
    )

    report_str = json.dumps(report)
    assert private_token not in report_str
    assert "hierarchy" not in report_str.lower() or "<?xml" not in report_str


def test_live_dry_run_discovery_mode(mock_device_env, monkeypatch):
    """Discovery mode returns observed IDs with calibrated=false and status=discovered."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        SWITCHER_XML,
        PAGE_PROFILE_XML,
        PAGE_SETTINGS_XML,
        PAGE_PROFILE_XML,
        PAGE_COMPOSER_XML,
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
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    report = run_live_dry_run(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        target_name=TEST_PAGE_NAME,
        discovery=True,
    )

    assert report["success"] is True
    assert report["calibrated"] is False
    assert report["status"] == "discovered"
    assert report["evidence"]["verified_main_profile_id"] == TEST_ROOT_ID
    assert report["evidence"]["verified_actor_id"] == TEST_PAGE_ID


def test_publish_fails_before_submit_when_modern_ui_has_no_direct_ids(mock_device_env, monkeypatch):
    """Publish must fail before typing or submit when modern UI lacks direct IDs."""
    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: fake_probe)
    monkeypatch.setattr("phone_runner.publish.verify_mobilerun_service", lambda s: (True, "pong"))
    monkeypatch.setattr("phone_runner.publish.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Opened composer", ""))

    # UI hierarchy has modern XML with no direct IDs (only display name)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: AdbResult(0, PAGE_COMPOSER_XML, ""))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_PAGE_ID, "name": TEST_PAGE_NAME, "kind": "facebook_page"},
        caption="Publish test",
    )

    assert res["status"] == "failed"
    assert res["error_code"] in ("identity_mismatch", "page_missing_parent_proof")
    assert res["publish_may_have_started"] is False


def test_geometry_clipping_and_scrolling_1920_2400_2408():
    """Validates viewport geometry extraction and clipping detection across 1920, 2400, 2408 resolutions."""
    import xml.etree.ElementTree as ET
    from phone_runner.identity_proof import extract_viewport_geometry, is_clipped

    # Test geometry strings from probe
    assert extract_viewport_geometry(None, "1080x1920") == (1080, 1920)
    assert extract_viewport_geometry(None, "1080x2400") == (1080, 2400)
    assert extract_viewport_geometry(None, "1080x2408") == (1080, 2408)

    # Test XML bounds extraction
    xml_1920 = ET.fromstring('<hierarchy><node bounds="[0,0][1080,1920]" /></hierarchy>')
    assert extract_viewport_geometry(xml_1920, None) == (1080, 1920)
    assert extract_viewport_geometry(xml_1920, "1080x1920") == (1080, 1920)

    # Inconsistent XML vs expected must raise ValueError
    with pytest.raises(ValueError, match="inconsistency"):
        extract_viewport_geometry(xml_1920, "1080x2408")

    # Missing geometry must raise ValueError
    with pytest.raises(ValueError, match="Missing actual"):
        extract_viewport_geometry(None, None)

    # 1920 screen: button ending at y=1850 is clipped (> 1920 - 100 = 1820)
    btn_clipped_1920 = (0, 1800, 1080, 1850)
    assert is_clipped(btn_clipped_1920, screen_height=1920) is True

    # 2400 screen: button ending at y=2350 is clipped (> 2400 - 100 = 2300)
    btn_clipped_2400 = (0, 2200, 1080, 2350)
    assert is_clipped(btn_clipped_2400, screen_height=2400) is True

    # 2408 screen: button ending at y=2350 is clipped (> 2408 - 100 = 2308)
    btn_clipped_2408 = (0, 2200, 1080, 2350)
    assert is_clipped(btn_clipped_2408, screen_height=2408) is True

    # Button safely in mid-screen is not clipped on any resolution
    safe_btn = (0, 500, 1080, 650)
    assert is_clipped(safe_btn, screen_height=1920) is False
    assert is_clipped(safe_btn, screen_height=2400) is False
    assert is_clipped(safe_btn, screen_height=2408) is False


def test_dry_run_not_blocked_by_incidental_switcher_ids(mock_device_env, monkeypatch):
    """Modern Katana UI with incidental switcher IDs in hierarchy does not block proof collector invocation."""
    from phone_runner.identity_proof import IdentityProofResult

    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: fake_probe)
    monkeypatch.setattr("phone_runner.dry_run.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    incidental_switcher_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana" bounds="[0,0][1080,2408]">
        <node class="android.widget.FrameLayout" bounds="[948,96][1068,228]" content-desc="Profile switcher account 100099999999999" package="com.facebook.katana" />
        <node class="android.widget.TextView" text="OwnPageTarget" package="com.facebook.katana" />
      </node>
    </hierarchy>"""
    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(0, incidental_switcher_xml, ""))
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, "com.facebook.katana/.FeedActivity", ""))

    collector_called = []
    fake_proof = IdentityProofResult(
        nonce="test_nonce_123",
        created_at="2026-10-15T12:00:00Z",
        serial=TEST_SERIAL,
        method="own_url_chain_v1",
        evidence_source="adb_device_read",
        observed_root_id=TEST_ROOT_ID,
        observed_root_url=f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        observed_target_id=TEST_PAGE_ID,
        observed_target_url=f"https://www.facebook.com/profile.php?id={TEST_PAGE_ID}",
        verified_main_profile_id=TEST_ROOT_ID,
        active_composer_actor_id=TEST_PAGE_ID,
        app_card_version=None,
        facebook_version="580.0.0.51.74",
        portal_version="0.7.25",
        resolution="1080x2408",
        rotation=0,
        steps=[],
        passed=True,
        stages=[
            {"stage": "root_url", "passed": True},
            {"stage": "hierarchy", "passed": True},
            {"stage": "target_url", "passed": True},
            {"stage": "composer_binding", "passed": True},
        ],
        composer_readiness={"has_header": True, "author_matched": True},
    )
    def mock_collector(**kwargs):
        collector_called.append(kwargs)
        return fake_proof

    monkeypatch.setattr("phone_runner.dry_run.collect_own_url_proof_chain", mock_collector)

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        target_page={"id": TEST_PAGE_ID, "name": TEST_PAGE_NAME},
    )

    assert len(collector_called) == 1
    assert res["success"] is True
    assert res["status"] == "ready"
    assert res["evidence"]["verified_actor_id"] == TEST_PAGE_ID


def test_dry_run_direct_legacy_spoof_rejected_immediately(mock_device_env, monkeypatch):
    """Direct legacy IDs in evidence that conflict with expected IDs fail closed without calling collector."""
    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: fake_probe)
    monkeypatch.setattr("phone_runner.dry_run.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    spoofed_activity = "cmp=com.facebook.katana/.FeedActivity c_user=999999999999"
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, spoofed_activity, ""))
    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(0, PAGE_COMPOSER_XML, ""))

    collector_called = []
    monkeypatch.setattr("phone_runner.dry_run.collect_own_url_proof_chain", lambda **kwargs: collector_called.append(kwargs))

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        target_page={"id": TEST_PAGE_ID, "name": TEST_PAGE_NAME},
    )

    assert len(collector_called) == 0
    assert res["success"] is False
    assert res["status"] == "identity_unverified"


def test_discovery_mode_page_same_name_as_root(mock_device_env, monkeypatch):
    """Discovery on a Page with same display name as root discovers the Page, never the selected root."""
    same_name = "AcmeBrand"
    same_name_switcher_xml = f"""<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" selected="true" bounds="[0,500][1080,650]" package="com.facebook.katana">
          <node text="{same_name}" content-desc="{same_name} profile" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,650][1080,800]" package="com.facebook.katana">
          <node text="{same_name}" content-desc="{same_name}" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""

    same_name_root_profile = ROOT_PROFILE_XML.replace("OwnRootUser", same_name)
    same_name_page_profile = PAGE_PROFILE_XML.replace("OwnPageTarget", same_name)
    same_name_composer = PAGE_COMPOSER_XML.replace("OwnPageTarget", same_name)

    ui_dumps = [
        same_name_root_profile,
        ROOT_SETTINGS_XML,
        same_name_switcher_xml,
        same_name_page_profile,
        PAGE_SETTINGS_XML,
        same_name_page_profile,
        same_name_composer,
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
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    report = run_live_dry_run(
        serial=TEST_SERIAL,
        main_name=same_name,
        target_name=same_name,
        discovery=True,
    )

    assert report["success"] is True
    assert report["status"] == "discovered"
    assert report["evidence"]["verified_main_profile_id"] == TEST_ROOT_ID
    assert report["evidence"]["verified_actor_id"] == TEST_PAGE_ID


def test_dry_run_initial_nav_fails_but_fresh_collector_succeeds(mock_device_env, monkeypatch):
    """If preliminary composer navigation times out or fails, collector still invokes and can succeed."""
    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: fake_probe)
    monkeypatch.setattr(
        "phone_runner.dry_run.run_mobilerun",
        lambda **kwargs: MobilerunResult(-1, "", "Command timed out after 60 seconds"),
    )
    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(0, PAGE_COMPOSER_XML, ""))
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, "com.facebook.katana/.FeedActivity", ""))

    collector_called = []
    fake_proof = IdentityProofResult(
        nonce="test_nonce_nav_retry",
        created_at="2026-10-15T12:00:00Z",
        serial=TEST_SERIAL,
        method="own_url_chain_v1",
        evidence_source="adb_device_read",
        observed_root_id=TEST_ROOT_ID,
        observed_root_url=f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        observed_target_id=TEST_PAGE_ID,
        observed_target_url=f"https://www.facebook.com/profile.php?id={TEST_PAGE_ID}",
        verified_main_profile_id=TEST_ROOT_ID,
        active_composer_actor_id=TEST_PAGE_ID,
        app_card_version=None,
        facebook_version="580.0.0.51.74",
        portal_version="0.7.25",
        resolution="1080x2408",
        rotation=0,
        steps=[],
        passed=True,
        stages=[
            {"stage": "root_url", "passed": True},
            {"stage": "hierarchy", "passed": True},
            {"stage": "target_url", "passed": True},
            {"stage": "composer_binding", "passed": True},
        ],
        composer_readiness={"has_header": True, "author_matched": True},
    )
    monkeypatch.setattr("phone_runner.dry_run.collect_own_url_proof_chain", lambda **kwargs: (collector_called.append(kwargs), fake_proof)[1])

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        target_page={"id": TEST_PAGE_ID, "name": TEST_PAGE_NAME},
    )

    assert len(collector_called) == 1
    assert res["success"] is True
    assert res["status"] == "ready"
    assert res["evidence"]["verified_actor_id"] == TEST_PAGE_ID


def test_dry_run_initial_nav_fails_and_collector_fails(mock_device_env, monkeypatch):
    """If preliminary nav fails and collector also fails, dry run fails with structured diagnostics without raw stderr."""
    fake_probe = {
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
        "screen": "awake",
        "resolution": "1080x2408",
        "rotation": 0,
        "facebook_version": "580.0.0.51.74",
        "portal_version": "0.7.25",
        "checked_at": "2026-10-14T12:00:00Z",
    }
    monkeypatch.setattr("phone_runner.dry_run.probe_device", lambda s, wake=False: fake_probe)
    raw_private_stderr = "Private token in stderr: 123456789"
    monkeypatch.setattr(
        "phone_runner.dry_run.run_mobilerun",
        lambda **kwargs: MobilerunResult(-1, "", f"Timed out. {raw_private_stderr}"),
    )
    monkeypatch.setattr("phone_runner.dry_run.query_ui_hierarchy", lambda s: AdbResult(0, PAGE_COMPOSER_XML, ""))
    monkeypatch.setattr("phone_runner.dry_run.query_activity_state", lambda s: AdbResult(0, "com.facebook.katana/.FeedActivity", ""))

    fake_failing_proof = IdentityProofResult(
        nonce="test_nonce_fail",
        created_at="2026-10-15T12:00:00Z",
        serial=TEST_SERIAL,
        method="own_url_chain_v1",
        evidence_source="adb_device_read",
        observed_root_id=None,
        observed_root_url=None,
        observed_target_id=None,
        observed_target_url=None,
        verified_main_profile_id=None,
        active_composer_actor_id=None,
        app_card_version=None,
        facebook_version="580.0.0.51.74",
        portal_version="0.7.25",
        resolution="1080x2408",
        rotation=0,
        steps=[],
        passed=False,
        stages=[
            {"stage": "root_url", "passed": False, "detail": "Screen title is not 'Profile Settings'"},
        ],
        composer_readiness={},
        error="Screen title is not 'Profile Settings'",
    )
    monkeypatch.setattr("phone_runner.dry_run.collect_own_url_proof_chain", lambda **kwargs: fake_failing_proof)

    res = execute_dry_run(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        target_page={"id": TEST_PAGE_ID, "name": TEST_PAGE_NAME},
    )

    assert res["success"] is False
    assert res["status"] == "identity_unverified"
    res_str = json.dumps(res)
    assert raw_private_stderr not in res_str


SEARCH_OVERLAY_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.EditText" clickable="true" bounds="[180,96][1044,228]" text="Search in your posts, photos and tags" content-desc="Search in your posts, photos and tags" package="com.facebook.katana" />
    <node class="android.widget.TextView" clickable="false" bounds="[36,987][1044,1131]" text="Enter a few words to search this profile." content-desc="Enter a few words to search this profile." package="com.facebook.katana" />
  </node>
</hierarchy>"""

AFTER_SEARCH_SETTINGS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[361,134][719,193]" text="Profile Settings" content-desc="Profile Settings" package="com.facebook.katana" />
    <node class="android.widget.FrameLayout" clickable="true" bounds="[948,96][1068,228]" text="" content-desc="Profile switcher, Double tap to open a list of your profiles" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1941][1080,2085]" text="" content-desc="Search" package="com.facebook.katana" />
    <node class="android.view.ViewGroup" bounds="[36,2132][1044,2203]" text="Sharing" content-desc="Sharing" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,2224][1080,2276]" text="" content-desc="Copy profile link" package="com.facebook.katana" />
  </node>
</hierarchy>"""

REAL_BOTTOM_SHEET_SWITCHER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1403][1080,1622]" content-desc="AnonRootUser. See your profile" package="com.facebook.katana">
      <node class="android.widget.ImageView" bounds="[36,1451][156,1571]" content-desc="Profile picture" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1463][936,1512]" text="AnonRootUser" content-desc="AnonRootUser" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1520][936,1562]" text="See your profile" content-desc="See your profile" package="com.facebook.katana" />
    </node>
    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1622][1080,1841]" content-desc="AnonPageTarget. 10 notifications" package="com.facebook.katana">
      <node class="android.widget.ImageView" bounds="[36,1670][156,1790]" content-desc="Profile picture" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[192,1682][1044,1731]" text="AnonPageTarget" content-desc="AnonPageTarget" package="com.facebook.katana" />
      <node class="android.widget.TextView" bounds="[228,1739][539,1781]" text="10 notifications" content-desc="10 notifications" package="com.facebook.katana" />
    </node>
    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,1841][1080,2060]" content-desc="OtherPage. 1 notification" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,1901][1044,1950]" text="OtherPage" content-desc="OtherPage" package="com.facebook.katana" />
    </node>
    <node class="android.view.ViewGroup" clickable="true" selected="false" bounds="[0,2060][1080,2276]" content-desc="Create new profile or Page" package="com.facebook.katana">
      <node class="android.widget.TextView" bounds="[192,2150][1044,2187]" text="Create new profile or Page" content-desc="Create new profile or Page" package="com.facebook.katana" />
    </node>
  </node>
</hierarchy>"""

PAGE_FEED_WITH_GO_TO_PROFILE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,300]" package="com.facebook.katana">
      <node class="android.widget.Button" clickable="true" bounds="[700,120][1040,240]" text="Go to profile" content-desc="Go to profile" package="com.facebook.katana" />
    </node>
  </node>
</hierarchy>"""


def test_reassert_portal_after_mobilerun_restores_default(mock_device_env, monkeypatch):
    """If Mobilerun CLI resets default IME back to LatinIME on exit, collector reasserts Portal IME with readback."""
    adb_recorder = AdbCommandRecorder()
    adb_recorder.current_ime = "com.google.android.inputmethod.latin/.LatinIME\n"

    def fake_nav_restoring_latin(**kwargs):
        # Simulate Mobilerun CLI resetting default IME upon exit
        adb_recorder.current_ime = "com.google.android.inputmethod.latin/.LatinIME\n"
        return MobilerunResult(0, "Navigated", "")

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
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", fake_nav_restoring_latin)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is True
    # Asserts Portal IME set was invoked after navigation
    portal_set_calls = [cmd for cmd in adb_recorder.commands if cmd[:3] == ["shell", "ime", "set"] and "com.mobilerun.portal" in cmd[3]]
    assert len(portal_set_calls) >= 2
    # Asserts that in finally block, original LatinIME was restored
    assert adb_recorder.current_ime == "com.google.android.inputmethod.latin/.LatinIME\n"


def test_search_keyboard_two_backs_restores_settings(mock_device_env, monkeypatch):
    """Settings Search IME binding uses two Back presses (overlay dismissal + search exit) to return to Settings."""
    dumps = [
        SEARCH_OVERLAY_XML,
        AFTER_SEARCH_SETTINGS_XML,
    ]
    dump_iter = iter(dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(dump_iter), ""))

    read_attempts = [0]
    def mock_read(s, expected_sentinel=None):
        read_attempts[0] += 1
        if read_attempts[0] == 1:
            return AdbResult(-1, "", "Error: Mobilerun keyboard is not active")
        return AdbResult(0, "sentinel_ok", "")

    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    taps = []
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", lambda args, serial=None: (taps.append(args), AdbResult(0, "OK", ""))[1])

    bound, did_bind, tree, raw_xml = bind_portal_ime_if_needed(TEST_SERIAL, AFTER_SEARCH_SETTINGS_XML, is_page=False)

    assert bound is True
    assert did_bind is True
    keyevents = [t for t in taps if t[:3] == ["shell", "input", "keyevent"]]
    assert len(keyevents) == 2


def test_real_bottomsheet_see_your_profile_and_hierarchy_gate(mock_device_env, monkeypatch):
    """Real bottomsheet switcher with 'See your profile' and candidate notifications succeeds; hierarchy gate deferred."""
    anon_root_name = "AnonRootUser"
    anon_page_name = "AnonPageTarget"
    anon_root_profile = ROOT_PROFILE_XML.replace("OwnRootUser", anon_root_name)
    anon_page_profile = PAGE_PROFILE_XML.replace("OwnPageTarget", anon_page_name)
    anon_page_composer = PAGE_COMPOSER_XML.replace("OwnPageTarget", anon_page_name)

    ui_dumps = [
        anon_root_profile,
        ROOT_SETTINGS_XML,
        REAL_BOTTOM_SHEET_SWITCHER_XML,
        anon_page_profile,
        PAGE_SETTINGS_XML,
        anon_page_profile,
        anon_page_composer,
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
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=anon_root_name,
        expected_root_id=TEST_ROOT_ID,
        target_name=anon_page_name,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_PAGE_ID
    hierarchy_stages = [s for s in proof.stages if s["stage"] == "hierarchy"]
    assert len(hierarchy_stages) == 1
    assert hierarchy_stages[0]["passed"] is True


def test_page_feed_go_to_profile_button_navigates_to_own_page(mock_device_env, monkeypatch):
    """Switching to Page landing on Feed taps unique 'Go to profile' once before reaching own Page profile."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        SWITCHER_XML,
        PAGE_FEED_WITH_GO_TO_PROFILE_XML,  # Switcher lands on Feed
        PAGE_PROFILE_XML,                  # After Go to profile tap
        PAGE_SETTINGS_XML,
        PAGE_PROFILE_XML,
        PAGE_COMPOSER_XML,
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
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is True
    # Go to profile button center is (870, 180)
    taps = [cmd for cmd in adb_recorder.commands if cmd[:3] == ["shell", "input", "tap"]]
    assert any(cmd[3] == "870" and cmd[4] == "180" for cmd in taps)


def test_duplicate_or_unknown_root_or_target_in_switcher_fails():
    """Switcher list rejects unknown root, unknown target, duplicate root, and duplicate target."""
    import xml.etree.ElementTree as ET

    tree = ET.fromstring(REAL_BOTTOM_SHEET_SWITCHER_XML)

    # Unknown root
    ok, err, _ = find_switcher_target_page(tree, "AnonPageTarget", "UnknownRoot")
    assert ok is False
    assert "Root profile 'UnknownRoot' not verified" in err

    # Unknown target
    ok, err, _ = find_switcher_target_page(tree, "UnknownTarget", "AnonRootUser")
    assert ok is False
    assert "Target page 'UnknownTarget' not found" in err

    # Duplicate root rows with See your profile
    dup_root_xml = REAL_BOTTOM_SHEET_SWITCHER_XML.replace(
        'OtherPage. 1 notification', 'AnonRootUser. See your profile'
    ).replace('OtherPage', 'AnonRootUser')
    dup_root_tree = ET.fromstring(dup_root_xml)
    ok, err, _ = find_switcher_target_page(dup_root_tree, "AnonPageTarget", "AnonRootUser")
    assert ok is False
    assert "Ambiguous switcher rows" in err

    # Duplicate target rows
    dup_target_xml = REAL_BOTTOM_SHEET_SWITCHER_XML.replace(
        'OtherPage. 1 notification', 'AnonPageTarget. 10 notifications'
    ).replace('OtherPage', 'AnonPageTarget')
    dup_target_tree = ET.fromstring(dup_target_xml)
    ok, err, _ = find_switcher_target_page(dup_target_tree, "AnonPageTarget", "AnonRootUser")
    assert ok is False
    assert "Ambiguous switcher rows" in err


def test_deadline_expiry_after_root_url_fails_closed(mock_device_env, monkeypatch):
    """Operation exceeding deadline after root resolution fails closed before switcher action."""
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        SWITCHER_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))

    mock_clear, mock_read = make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        f"https://www.facebook.com/profile.php?id={TEST_PAGE_ID}",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", AdbCommandRecorder())
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    now = [1000.0]
    def fake_time():
        now[0] += 50.0  # Advances time rapidly so deadline is exceeded after root url
        return now[0]

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
        timeout=100.0,
        time_fn=fake_time,
    )

    assert proof.passed is False
    assert "deadline exceeded" in (proof.error or "").lower()


def test_end_probe_drift_rejected(mock_device_env, monkeypatch):
    """Drift in resolution, rotation, or version between baseline and end probe fails proof."""
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
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", AdbCommandRecorder())
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    probe_calls = [0]
    def drifting_probe(serial, wake=False):
        probe_calls[0] += 1
        base = {
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
        if probe_calls[0] > 1:
            # End probe drifts
            base["facebook_version"] = "581.0.0.0.0"
        return base

    monkeypatch.setattr("phone_runner.identity_proof.probe_device", drifting_probe)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is False
    assert "version drifted" in (proof.error or "").lower()


def test_actual_observed_fields_fidelity(mock_device_env, monkeypatch):
    """IdentityProofResult populates observed fields directly without claiming unobserved parameters."""
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
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", AdbCommandRecorder())
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is True
    assert proof.serial == TEST_SERIAL
    assert proof.resolution == "1080x2408"
    assert proof.rotation == 0
    assert proof.facebook_version == "580.0.0.51.74"
    assert proof.portal_version == "0.7.25"
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_ROOT_ID
    assert proof.observed_root_url == f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}"
    assert proof.evidence_source == "adb_device_read"
    assert proof.method == "own_url_chain_v1"


def test_reassert_failure_does_not_leak_raw_stderr(mock_device_env, monkeypatch):
    """If Portal IME reassertion fails with sensitive stderr, error message does not echo it."""
    private_secret = "sensitive_ime_error_token_987654321"

    adb_recorder = AdbCommandRecorder()
    ime_set_calls = [0]
    def failing_reassert_adb(args, serial=None):
        if len(args) >= 3 and args[1:3] == ["ime", "set"]:
            ime_set_calls[0] += 1
            if ime_set_calls[0] == 2:
                # Fail during reassert_selected (second ime set call)
                return AdbResult(-1, "", f"Private IME error: {private_secret}")
        return adb_recorder(args, serial=serial)

    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        ROOT_COMPOSER_XML,
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(ui_iter), ""))
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", failing_reassert_adb)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
    )

    assert proof.passed is False
    assert proof.error == "Failed to reassert Portal IME after navigation"
    assert private_secret not in (proof.error or "")


def test_ime_set_wrong_readback_fails(mock_device_env, monkeypatch):
    """If default_input_method readback does not match exact Portal component, fails closed."""
    adb_recorder = AdbCommandRecorder()
    # Mock settings get secure default_input_method to return a different IME
    def mismatched_readback_adb(args, serial=None):
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            # Returns wrong IME even after ime set
            return AdbResult(0, "com.other.ime/.OtherIME\n", "")
        return adb_recorder(args, serial=serial)

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", mismatched_readback_adb)

    ctx = PortalImeContext(TEST_SERIAL)
    with pytest.raises(RuntimeError) as exc_info:
        ctx.__enter__()
    assert "Portal IME readback mismatch" in str(exc_info.value)


def test_failure_after_target_proof_leaves_active_composer_actor_none(mock_device_env, monkeypatch):
    """If target Page URL proof passes but composer trigger fails, active_composer_actor_id remains None."""
    page_profile_without_composer = PAGE_PROFILE_XML.replace("What's on your mind?", "No trigger")

    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        SWITCHER_XML,
        PAGE_PROFILE_XML,
        PAGE_SETTINGS_XML,
        page_profile_without_composer,  # Return to Page profile, but composer trigger is missing
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_ui_hierarchy",
        lambda s: AdbResult(0, next(ui_iter, page_profile_without_composer), ""),
    )

    mock_clear, mock_read = make_mock_clipboard([
        f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        f"https://www.facebook.com/profile.php?id={TEST_PAGE_ID}",
    ])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", AdbCommandRecorder())
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_PAGE_NAME,
        expected_target_id=TEST_PAGE_ID,
    )

    assert proof.passed is False
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_PAGE_ID
    # Critical evidence invariant: active_composer_actor_id MUST remain None
    assert proof.active_composer_actor_id is None
    assert "Cannot find composer trigger on profile" in (proof.error or "")


def test_realistic_anchor_child_false_parent_true():
    """Anchor node with clickable=false inside clickable=true parent taps anchor bounds avoiding sibling Photo."""
    import xml.etree.ElementTree as ET

    ayok_composer_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,881][1080,1073]" package="com.facebook.katana">
          <node class="android.widget.ImageView" bounds="[36,917][156,1037]" content-desc="Profile picture icon" package="com.facebook.katana" />
          <node class="android.widget.Button" clickable="false" bounds="[156,944][936,1009]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
          <node class="android.widget.Button" clickable="true" bounds="[936,923][1044,1031]" content-desc="Photo" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""

    tree = ET.fromstring(ayok_composer_xml)
    ok, err, bounds = find_composer_open_trigger(tree)
    assert ok is True
    # Asserts returned bounds are exactly the anchor's own bounds [156,944][936,1009], avoiding [936,923][1044,1031]
    assert bounds == (156, 944, 936, 1009)


def test_switcher_rejects_duplicate_root_names_regardless_of_selected_state():
    """Switcher list rejects duplicate root name rows even if only one has selected=true."""
    import xml.etree.ElementTree as ET

    dup_root_one_selected_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OwnRootUser" content-desc="OwnRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" selected="false" package="com.facebook.katana">
          <node text="OwnRootUser" content-desc="OwnRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""

    tree = ET.fromstring(dup_root_one_selected_xml)
    ok, err, bounds = find_switcher_target_page(tree, "OwnPageTarget", "OwnRootUser")
    assert ok is False
    assert "Ambiguous switcher rows" in err


def test_switcher_rejects_different_active_root_and_inconsistent_roots():
    """Switcher list rejects when a different root is active or multiple roots claim active."""
    import xml.etree.ElementTree as ET

    different_active_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OtherRootUser" content-desc="OtherRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" package="com.facebook.katana">
          <node text="OwnRootUser" content-desc="OwnRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""

    tree1 = ET.fromstring(different_active_xml)
    ok1, err1, _ = find_switcher_target_page(tree1, "OwnPageTarget", "OwnRootUser")
    assert ok1 is False
    assert "Different root session is active" in err1

    inconsistent_active_xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,400][1080,500]" selected="true" package="com.facebook.katana">
          <node text="OtherRootUser" content-desc="OtherRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,500][1080,600]" selected="true" package="com.facebook.katana">
          <node text="OwnRootUser" content-desc="OwnRootUser" package="com.facebook.katana" />
        </node>
        <node class="android.view.ViewGroup" clickable="true" bounds="[0,600][1080,700]" package="com.facebook.katana">
          <node text="OwnPageTarget" content-desc="OwnPageTarget" package="com.facebook.katana" />
          <node text="Page" content-desc="Page" package="com.facebook.katana" />
        </node>
      </node>
    </hierarchy>"""

    tree2 = ET.fromstring(inconsistent_active_xml)
    ok2, err2, _ = find_switcher_target_page(tree2, "OwnPageTarget", "OwnRootUser")
    assert ok2 is False
    assert "Inconsistent switcher state" in err2


def test_verify_foreground_rejects_active_other_with_historical_fb(mock_device_env, monkeypatch):
    """Historical com.facebook.katana in activity dump does not fool foreground verification when top activity is non-FB."""
    launcher_window_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{12345 u0 com.android.launcher3/com.android.launcher3.uioverrides.QuickstepLauncher}\n"
        "mFocusedApp=AppWindowToken{... token=... u0 com.android.launcher3/...}"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, launcher_window_dump, ""))
    historical_dump = """
      Stack #0: type=home
        Task id #1
          ACTIVITY com.android.launcher3/.uioverrides.QuickstepLauncher 1234
      Historical:
        Task id #2
          A=com.facebook.katana
    """
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, historical_dump, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, ROOT_PROFILE_XML, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is False
    assert "com.android.launcher3" in err


def test_verify_foreground_polls_notification_shade_transition(mock_device_env, monkeypatch):
    """Transient NotificationShade focus right after wake polls and recovers once FB gains focus."""
    window_dumps = [
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{cb5bf64 u0 NotificationShade}",
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",
    ]
    w_iter = iter(window_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, next(w_iter), ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "ACTIVITY com.facebook.katana/.activity.FbMainTabActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, ROOT_PROFILE_XML, ""))

    ok, err, tree, raw = verify_foreground_facebook(TEST_SERIAL)
    assert ok is True


def test_pre_proof_gallery_launches_fb_then_proves_ownroot(mock_device_env, monkeypatch):
    """When device is in Gallery before collector runs, collector launches FB feed and proves own root."""
    window_dumps = [
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{12345 u0 com.coloros.gallery3d/com.coloros.gallery3d.app.MainActivity}",  # before initial nav
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # root profile check
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # root settings check
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # return to root profile
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # composer capture
    ]
    w_iter = iter(window_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, next(w_iter, window_dumps[-1]), ""))

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

    launch_calls = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_feed",
        lambda s: (launch_calls.append(s), AdbResult(0, "OK", ""))[1],
    )

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is True
    assert launch_calls == [TEST_SERIAL]


def test_gallery_after_url_proof_aborts_without_relaunching_fb(mock_device_env, monkeypatch):
    """If Gallery appears after URL proof is resolved, proof aborts immediately and does not relaunch FB."""
    window_dumps = [
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # init
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # root profile check
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\nmFocusedApp=... com.facebook.katana",  # root settings check
        # After URL proof: device enters Gallery!
        "mCurrentRotation=ROTATION_0\nmCurrentFocus=Window{12345 u0 com.coloros.gallery3d/com.coloros.gallery3d.app.MainActivity}",
    ]
    w_iter = iter(window_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, next(w_iter, window_dumps[-1]), ""))

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

    launch_calls = []
    monkeypatch.setattr(
        "phone_runner.identity_proof.launch_facebook_feed",
        lambda s: (launch_calls.append(s), AdbResult(0, "OK", ""))[1],
    )

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is False
    assert proof.active_composer_actor_id is None
    assert proof.observed_root_id == TEST_ROOT_ID
    assert any(s.get("stage") == "root_url" and s.get("passed") is True for s in proof.stages)
    assert "Return to Root profile failed" in (proof.error or "")
    assert len(launch_calls) == 0
    start_cmds = [c for c in adb_recorder.commands if c[:3] == ["shell", "am", "start"]]
    assert len(start_cmds) == 0


def test_own_profile_with_messages_before_edit_profile_passes():
    xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[100,100][200,200]" text="Messages" content-desc="Messages" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="OwnRootUser" content-desc="" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" />
    </hierarchy>"""
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (924, 266, 1032, 374)


def test_own_profile_with_post_text_send_me_a_message_passes():
    xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana" class="android.widget.TextView" clickable="false" bounds="[50,500][500,600]" text="Send me a message" content-desc="" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="OwnRootUser" content-desc="" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[708,266][816,374]" text="" content-desc="Edit profile" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" />
    </hierarchy>"""
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is True
    assert err == ""
    assert more_bounds == (924, 266, 1032, 374)


def test_visitor_profile_with_clickable_add_friend_and_message_fails():
    xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="OwnRootUser" content-desc="" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[100,200][300,300]" text="Add friend" content-desc="Add friend" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[350,200][550,300]" text="Message" content-desc="Message" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[924,266][1032,374]" text="" content-desc="More profile settings" />
      <node package="com.facebook.katana" class="android.widget.Button" clickable="true" bounds="[36,1713][530,1821]" text="" content-desc="Dashboard" />
    </hierarchy>"""
    tree = ET.fromstring(xml)
    ok, err, more_bounds = find_own_profile_controls(tree, "OwnRootUser")
    assert ok is False
    assert err == "Detected visitor profile rather than own profile"
    assert more_bounds is None


def test_collect_own_url_proof_chain_polls_profile_settings_success(mock_device_env, monkeypatch):
    ui_dumps = [
        ROOT_PROFILE_XML,   # step 2 root profile check
        ROOT_PROFILE_XML,   # poll 1 after tap (old screen)
        ROOT_PROFILE_XML,   # poll 2 after tap (old screen)
        ROOT_SETTINGS_XML,  # poll 3 after tap (profile settings ready)
        ROOT_PROFILE_XML,   # return to root profile
        ROOT_COMPOSER_XML,  # composer binding
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
    sleep_calls = []
    monkeypatch.setattr("time.sleep", lambda s: sleep_calls.append(s))

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        profile_url_proof=True,
    )

    assert proof.passed is True
    assert proof.observed_root_id == TEST_ROOT_ID
    assert len(sleep_calls) >= 3


def test_collect_own_url_proof_chain_settings_timeout_fails_and_saves_observation(mock_device_env, monkeypatch, tmp_path):
    obs_dir = tmp_path / "obs"
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(obs_dir))

    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, ROOT_PROFILE_XML, ""))

    adb_recorder = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_recorder)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kwargs: MobilerunResult(0, "Navigated", ""))

    clock = [1000.0]
    def fake_sleep(s):
        clock[0] += s
    monkeypatch.setattr("time.sleep", fake_sleep)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        target_name=TEST_ROOT_NAME,
        expected_target_id=TEST_ROOT_ID,
        time_fn=lambda: clock[0],
        profile_url_proof=True,
    )

    assert proof.passed is False
    assert "Profile Settings controls invalid: Screen title is not 'Profile Settings'" in (proof.error or "")

    files = list(obs_dir.glob("*identity_gate*"))
    assert len(files) > 0
    xml_files = [f for f in files if f.suffix == ".xml"]
    assert len(xml_files) > 0
    saved_content = xml_files[0].read_text(encoding="utf-8")
    assert "OwnRootUser" in saved_content
