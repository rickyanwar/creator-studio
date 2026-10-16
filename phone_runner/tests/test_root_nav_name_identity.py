import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    _try_open_own_root_without_model,
    collect_own_url_proof_chain,
)
from phone_runner.mobilerun import MobilerunResult
from phone_runner.tests.test_identity_proof import (
    AdbCommandRecorder,
    make_mock_clipboard,
)

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "OwnRootUser"
TEST_ROOT_ID = "100012345678901"


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


def test_root_nav_allow_name_identity_true_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tree = ET.Element("hierarchy")
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda serial, expected_geometry=None: (True, "", fake_tree, "<hierarchy />"),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.find_own_profile_controls",
        lambda tree, main_name: (False, "x", None),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.find_own_profile_name_identity",
        lambda tree, main_name: (True, "ok"),
    )

    feed_called = False

    def fake_launch_feed(serial: str) -> None:
        nonlocal feed_called
        feed_called = True

    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", fake_launch_feed)

    res = _try_open_own_root_without_model(TEST_SERIAL, "Agung", allow_name_identity=True)
    assert res is True
    assert feed_called is False


def test_root_nav_allow_name_identity_false_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tree = ET.Element("hierarchy")
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda serial, expected_geometry=None: (True, "", fake_tree, "<hierarchy />"),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.find_own_profile_controls",
        lambda tree, main_name: (False, "x", None),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.find_own_profile_name_identity",
        lambda tree, main_name: (True, "ok"),
    )
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda serial: None)
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda serial: None)
    monkeypatch.setattr("phone_runner.identity_proof._check_and_tap_go_to_profile", lambda *a, **kw: False)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)

    res = _try_open_own_root_without_model(TEST_SERIAL, "Agung", allow_name_identity=False)
    assert res is False


def test_root_nav_old_path_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_tree = ET.Element("hierarchy")
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda serial, expected_geometry=None: (True, "", fake_tree, "<hierarchy />"),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.find_own_profile_controls",
        lambda tree, main_name: (True, "", (1, 2, 3, 4)),
    )

    res_false = _try_open_own_root_without_model(TEST_SERIAL, "Agung", allow_name_identity=False)
    assert res_false is True

    res_true = _try_open_own_root_without_model(TEST_SERIAL, "Agung", allow_name_identity=True)
    assert res_true is True


def test_model_nav_timeout_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)

    captured_timeout: float | None = None

    def fake_mobilerun(**kwargs) -> MobilerunResult:
        nonlocal captured_timeout
        captured_timeout = kwargs.get("timeout")
        return MobilerunResult(0, "Navigated", "")

    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", fake_mobilerun)

    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_power_state",
        lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""),
    )
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr(
        "phone_runner.identity_proof.query_activity_state",
        lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""),
    )
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)

    adb_rec = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_rec)

    tab_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=... com.facebook.katana\n"
    )
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, tab_dump, ""))
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, "<hierarchy />", ""))

    mock_clear, mock_read = make_mock_clipboard([f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}"])
    monkeypatch.setattr("phone_runner.identity_proof.clear_device_clipboard", mock_clear)
    monkeypatch.setattr("phone_runner.identity_proof.read_device_clipboard", mock_read)

    probe = {
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
    monkeypatch.setattr("phone_runner.identity_proof.probe_device", lambda s, wake=False: dict(probe))
    monkeypatch.setattr("phone_runner.identity_proof.get_local_app_card_version", lambda: "1.0.0")

    collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
        timeout=240.0,
    )

    assert captured_timeout is not None
    assert captured_timeout <= 120.0
    assert captured_timeout <= (240.0 - 60.0)


def test_composer_placeholder_counts_as_empty_input():
    from phone_runner.identity_proof import _is_empty_input_text

    assert _is_empty_input_text("") is True
    assert _is_empty_input_text("What's on your mind?") is True
    assert _is_empty_input_text("  Apa yang Anda pikirkan?  ") is True
    assert _is_empty_input_text("Marc Márquez is proving") is False


def test_wait_for_window_focus_polls_until_composer(monkeypatch):
    from phone_runner import identity_proof
    from phone_runner.adb import AdbResult

    focus = iter([
        "mCurrentFocus=Window{1 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}",
        "mCurrentFocus=Window{2 u0 com.facebook.katana/com.facebook.composer.activity.ComposerActivity}",
    ])
    monkeypatch.setattr(identity_proof.time, "sleep", lambda s: None)
    monkeypatch.setattr(identity_proof, "run_adb", lambda args, serial=None: AdbResult(0, next(focus), ""))
    assert identity_proof._wait_for_window_focus("100.64.96.50:5555", "ComposerActivity") is True


def test_wait_for_window_focus_times_out(monkeypatch):
    from phone_runner import identity_proof
    from phone_runner.adb import AdbResult

    clock = iter([0.0, 3.0, 9.0, 12.0])
    monkeypatch.setattr(identity_proof.time, "sleep", lambda s: None)
    monkeypatch.setattr(identity_proof.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        identity_proof,
        "run_adb",
        lambda args, serial=None: AdbResult(0, "mCurrentFocus=Window{1 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}", ""),
    )
    assert identity_proof._wait_for_window_focus("100.64.96.50:5555", "ComposerActivity", timeout=8.0) is False


def test_fb_profile_scrolled_down_is_scrolled_back_to_top(monkeypatch):
    import xml.etree.ElementTree as ET
    from phone_runner import identity_proof
    from phone_runner.adb import AdbResult

    scrolled = ET.fromstring('<hierarchy><node text="Manage posts" bounds="[0,0][10,10]"/></hierarchy>')
    header = ET.fromstring('<hierarchy><node text="Agung" bounds="[0,0][10,10]"/></hierarchy>')
    trees = iter([scrolled, scrolled, scrolled, scrolled, header])
    swipes = []

    monkeypatch.setattr(identity_proof.time, "sleep", lambda s: None)
    monkeypatch.setattr(identity_proof, "launch_facebook_feed", lambda s: None)
    monkeypatch.setattr(identity_proof, "launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr(identity_proof, "_check_and_tap_go_to_profile", lambda *a, **k: False)
    monkeypatch.setattr(identity_proof, "verify_foreground_facebook", lambda s, g=None: (True, "", next(trees), "<x/>"))
    monkeypatch.setattr(identity_proof, "find_own_profile_controls", lambda tree, name: (False, "x", None))
    monkeypatch.setattr(
        identity_proof,
        "find_own_profile_name_identity",
        lambda tree, name: (tree is header, "ok" if tree is header else "no header"),
    )
    monkeypatch.setattr(identity_proof, "run_adb", lambda args, serial=None: swipes.append(args) or AdbResult(0, "", ""))

    assert identity_proof._try_open_own_root_without_model("100.64.96.50:5555", "Agung", None, allow_name_identity=True) is True
    assert swipes == [["shell", "input", "swipe", "540", "700", "540", "1900", "300"]] * 2


def test_foreign_profile_without_manage_posts_is_never_scrolled(monkeypatch):
    import xml.etree.ElementTree as ET
    from phone_runner import identity_proof
    from phone_runner.adb import AdbResult

    foreign = ET.fromstring('<hierarchy><node text="Add friend" bounds="[0,0][10,10]"/></hierarchy>')
    cmds = []
    monkeypatch.setattr(identity_proof.time, "sleep", lambda s: None)
    monkeypatch.setattr(identity_proof, "launch_facebook_feed", lambda s: None)
    monkeypatch.setattr(identity_proof, "launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr(identity_proof, "_check_and_tap_go_to_profile", lambda *a, **k: False)
    monkeypatch.setattr(identity_proof, "verify_foreground_facebook", lambda s, g=None: (True, "", foreign, "<x/>"))
    monkeypatch.setattr(identity_proof, "find_own_profile_controls", lambda tree, name: (False, "x", None))
    monkeypatch.setattr(identity_proof, "find_own_profile_name_identity", lambda tree, name: (False, "visitor"))
    monkeypatch.setattr(identity_proof, "run_adb", lambda args, serial=None: cmds.append(args) or AdbResult(0, "", ""))

    assert identity_proof._try_open_own_root_without_model("100.64.96.50:5555", "Agung", None, allow_name_identity=True) is False
    assert cmds == []


def test_open_profile_switcher_is_closed_with_back_never_tapped(monkeypatch):
    import xml.etree.ElementTree as ET
    from phone_runner import identity_proof
    from phone_runner.adb import AdbResult

    feed = ET.fromstring('<hierarchy><node text="Feed" bounds="[0,0][10,10]"/></hierarchy>')
    switcher = ET.fromstring(
        '<hierarchy><node content-desc="Dismiss" bounds="[0,0][10,10]"/>'
        '<node text="GP Weekend" bounds="[0,20][10,30]"/><node text="Page" bounds="[0,30][10,40]"/></hierarchy>'
    )
    header = ET.fromstring('<hierarchy><node text="Agung" bounds="[0,0][10,10]"/></hierarchy>')
    trees = iter([feed, feed, switcher, header])
    cmds = []
    monkeypatch.setattr(identity_proof.time, "sleep", lambda s: None)
    monkeypatch.setattr(identity_proof, "launch_facebook_feed", lambda s: None)
    monkeypatch.setattr(identity_proof, "launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr(identity_proof, "_check_and_tap_go_to_profile", lambda *a, **k: False)
    monkeypatch.setattr(identity_proof, "verify_foreground_facebook", lambda s, g=None: (True, "", next(trees), "<x/>"))
    monkeypatch.setattr(identity_proof, "find_own_profile_controls", lambda tree, name: (False, "x", None))
    monkeypatch.setattr(identity_proof, "find_own_profile_name_identity", lambda tree, name: (tree is header, ""))
    monkeypatch.setattr(identity_proof, "run_adb", lambda args, serial=None: cmds.append(args) or AdbResult(0, "", ""))

    assert identity_proof._try_open_own_root_without_model("100.64.96.50:5555", "Agung", None, allow_name_identity=True) is True
    assert cmds == [["shell", "input", "keyevent", "4"]]
