import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    collect_own_url_proof_chain,
    find_own_profile_name_identity,
)
from phone_runner.mobilerun import MobilerunResult

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100012345678901"

PROFESSIONAL_PROFILE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[936,337][1068,469]" text="" content-desc="Edit cover photo" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[654,500][762,608]" text="" content-desc="Edit profile picture" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[396,669][606,751]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.view.View" clickable="false" bounds="[396,669][696,751]" text="Agung" content-desc="Agung Get verified" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[624,669][696,751]" text="" content-desc="Get verified" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1298][530,1406]" text="" content-desc="Dashboard" package="com.facebook.katana">
      <node class="android.view.View" clickable="false" bounds="[36,1298][530,1406]" text="Dashboard" content-desc="" package="com.facebook.katana" />
    </node>
    <node class="android.widget.Button" clickable="true" bounds="[554,1298][1044,1406]" text="Create" content-desc="Create" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1500][500,1600]" text="" content-desc="Edit Personal details" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[0,1700][1080,1850]" text="What's on your mind?" content-desc="What's on your mind?" package="com.facebook.katana" />
  </node>
</hierarchy>"""

COMPOSER_AGUNG_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" bounds="[0,96][1080,228]" text="New post" content-desc="New post" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,120][100,180]" content-desc="Close" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[150,250][450,300]" text="Agung" content-desc="Agung" package="com.facebook.katana" />
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


def test_professional_profile_matches_exact_name():
    """Professional profile fixture matches exact expected name 'Agung'."""
    tree = ET.fromstring(PROFESSIONAL_PROFILE_XML)
    ok, err = find_own_profile_name_identity(tree, "Agung")
    assert ok is True
    assert err == ""


def test_name_mismatch_and_substring_rejections():
    """Expected 'Agung Baelah' fails on 'Agung' fixture; 'Agung' fails when only 'Agung Baelah' exists."""
    tree = ET.fromstring(PROFESSIONAL_PROFILE_XML)
    ok, err = find_own_profile_name_identity(tree, "Agung Baelah")
    assert ok is False
    assert "Agung Baelah" in err

    # Case sensitivity check
    ok_case, err_case = find_own_profile_name_identity(tree, "agung")
    assert ok_case is False

    # Fixture where name node has 'Agung Baelah' instead of 'Agung'
    xml_baelah = PROFESSIONAL_PROFILE_XML.replace('text="Agung"', 'text="Agung Baelah"')
    tree_baelah = ET.fromstring(xml_baelah)
    ok_sub, err_sub = find_own_profile_name_identity(tree_baelah, "Agung")
    assert ok_sub is False
    assert "Agung" in err_sub


def test_visitor_fixture_rejected():
    """Profiles showing visitor buttons (Add friend, Message, Follow) must be rejected."""
    for marker in ("Add friend", "Message", "Follow", "Add Friend"):
        xml_visitor = f"""<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[936,337][1068,469]" text="" content-desc="Edit cover photo" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[396,669][606,751]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1298][530,1406]" text="Dashboard" content-desc="Dashboard" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[100,100][300,200]" text="{marker}" content-desc="" package="com.facebook.katana" />
  </node>
</hierarchy>"""
        tree_vis = ET.fromstring(xml_visitor)
        ok, err = find_own_profile_name_identity(tree_vis, "Agung")
        assert ok is False, f"Did not reject marker: {marker}"
        assert "visitor" in err.lower()


def test_missing_owner_controls_rejected():
    """Without at least one clickable owner control, identity must fail."""
    # No owner control
    xml_no_owner = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,669][606,751]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1298][530,1406]" text="Dashboard" content-desc="Dashboard" package="com.facebook.katana" />
  </node>
</hierarchy>"""
    tree_no_owner = ET.fromstring(xml_no_owner)
    ok, err = find_own_profile_name_identity(tree_no_owner, "Agung")
    assert ok is False
    assert "owner control" in err.lower()

    # Owner control present but clickable="false"
    xml_unclickable_owner = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="false" bounds="[936,337][1068,469]" text="" content-desc="Edit cover photo" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[396,669][606,751]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1298][530,1406]" text="Dashboard" content-desc="Dashboard" package="com.facebook.katana" />
  </node>
</hierarchy>"""
    tree_unclickable = ET.fromstring(xml_unclickable_owner)
    ok, err = find_own_profile_name_identity(tree_unclickable, "Agung")
    assert ok is False
    assert "owner control" in err.lower()


def test_missing_dashboard_rejected():
    """Without exact 'Dashboard' control, identity must fail."""
    # No Dashboard
    xml_no_dash = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[936,337][1068,469]" text="" content-desc="Edit cover photo" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[396,669][606,751]" text="Agung" content-desc="" package="com.facebook.katana" />
  </node>
</hierarchy>"""
    tree_no_dash = ET.fromstring(xml_no_dash)
    ok, err = find_own_profile_name_identity(tree_no_dash, "Agung")
    assert ok is False
    assert "Dashboard" in err

    # Substring 'Professional dashboard' does not satisfy exact 'Dashboard'
    xml_prof_dash = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[936,337][1068,469]" text="" content-desc="Edit cover photo" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[396,669][606,751]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[36,1298][530,1406]" text="" content-desc="Professional dashboard" package="com.facebook.katana" />
  </node>
</hierarchy>"""
    tree_prof_dash = ET.fromstring(xml_prof_dash)
    ok, err = find_own_profile_name_identity(tree_prof_dash, "Agung")
    assert ok is False
    assert "Dashboard" in err


def test_proof_chain_name_identity_profile_only_success(mock_device_env, monkeypatch):
    """Professional profile without More settings passes proof chain for profile-only target."""
    ui_dumps = [
        PROFESSIONAL_PROFILE_XML,
        COMPOSER_AGUNG_XML,
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

    assert proof.passed is True
    assert proof.method == "own_profile_name_v1"
    assert proof.observed_root_id == TEST_ROOT_ID
    assert proof.observed_target_id == TEST_ROOT_ID
    assert proof.observed_root_url is None
    assert proof.observed_target_url is None
    assert proof.verified_main_profile_id == TEST_ROOT_ID
    assert proof.active_composer_actor_id == TEST_ROOT_ID

    # Check stages
    stage_names = [s["stage"] for s in proof.stages]
    assert "root_url" in stage_names
    assert "target_url" in stage_names
    assert "composer_binding" in stage_names
    assert all(s["passed"] for s in proof.stages)

    root_stage = next(s for s in proof.stages if s["stage"] == "root_url")
    assert root_stage["detail"] == "name-based owner-profile proof (URL route unavailable)"
    target_stage = next(s for s in proof.stages if s["stage"] == "target_url")
    assert target_stage["detail"] == "name-based owner-profile proof (URL route unavailable)"

    # Check steps
    step_names = [s["step"] for s in proof.steps]
    assert "root_profile_name_verified" in step_names
    assert "composer_bound" in step_names
    name_step = next(s for s in proof.steps if s["step"] == "root_profile_name_verified")
    assert name_step["detail"] == "Own profile verified by exact name + owner controls (owner policy 2026-10-16)"

    # Assert no settings/copy-link taps and no back navigation happened
    for cmd in adb_recorder.commands:
        assert cmd[1:3] != ["input", "keyevent"], f"Unexpected keyevent in proof: {cmd}"
        if cmd[1:3] == ["input", "tap"]:
            # Only composer trigger should be tapped [540, 1775] center of [0, 1700][1080, 1850]
            assert cmd[3] == "540" and cmd[4] == "1775", f"Unexpected tap in proof: {cmd}"


def test_proof_chain_name_identity_page_target_fails_at_root_url(mock_device_env, monkeypatch):
    """When target is a Page, professional profile missing More settings still fails at root_url."""
    ui_dumps = [
        PROFESSIONAL_PROFILE_XML,
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
        target_name="MyPageTarget",
        expected_target_id="100098765432109",
    )

    assert proof.passed is False
    assert proof.method == "own_url_chain_v1"
    assert proof.stages[-1]["stage"] == "root_url"
    assert proof.stages[-1]["passed"] is False
    assert "Root own profile invalid" in (proof.error or "")
