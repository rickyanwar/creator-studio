"""Tests for measured Facebook 580 composer actor-title variant."""

import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    IdentityProofResult,
    collect_own_url_proof_chain,
    verify_composer_binding,
)
from phone_runner.mobilerun import MobilerunResult
from phone_runner.tests.test_identity_proof import (
    AdbCommandRecorder,
    ROOT_PROFILE_XML,
    ROOT_SETTINGS_XML,
    make_mock_clipboard,
)


COMPOSER_WINDOW_DUMP = (
    "mCurrentRotation=ROTATION_0\n"
    "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.composer.activity.ComposerActivity}\n"
    "mFocusedApp=AppWindowToken{1234 token=Token{abcd ActivityRecord{ef01 u0 com.facebook.katana/com.facebook.composer.activity.ComposerActivity t99}}}\n"
)

COMPOSER_WINDOW_DUMP_SHORT = (
    "mCurrentRotation=ROTATION_0\n"
    "mCurrentFocus=Window{605df14 u0 com.facebook.katana/.composer.activity.ComposerActivity}\n"
    "mFocusedApp=AppWindowToken{1234 token=Token{abcd ActivityRecord{ef01 u0 com.facebook.katana/.composer.activity.ComposerActivity t99}}}\n"
)

WRONG_ACTIVITY_WINDOW_DUMP = (
    "mCurrentRotation=ROTATION_0\n"
    "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
    "mFocusedApp=AppWindowToken{1234 token=Token{abcd ActivityRecord{ef01 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity t99}}}\n"
)

CONTRADICTORY_APP_WINDOW_DUMP = (
    "mCurrentRotation=ROTATION_0\n"
    "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.composer.activity.ComposerActivity}\n"
    "mFocusedApp=AppWindowToken{1234 token=Token{abcd ActivityRecord{ef01 u0 com.other.package/com.other.Activity t99}}}\n"
)


def build_measured_actor_title_xml(
    author: str = "OwnPageTarget",
    close_clickable: bool = True,
    close_enabled: bool = True,
    has_close: bool = True,
    has_next: bool = True,
    top_title_text: str | None = None,
    body_author_text: str | None = None,
    has_body: bool = True,
    toolbar_order: str = "close_title_next",
    body_bounds: str = "[252,325][529,384]",
    caption_count: int = 1,
    caption_text: str = "What's on your mind?",
    caption_bounds: str = "[0,690][1080,792]",
    package_name: str = "com.facebook.katana",
) -> str:
    if top_title_text is None:
        top_title_text = author
    if body_author_text is None:
        body_author_text = author

    # Toolbar bounds
    if toolbar_order == "close_title_next":
        close_b = "[30,108][138,216]"
        title_b = "[144,96][810,228]"
        title_child_b = "[413,134][668,193]"
        next_b = "[810,96][1068,204]"
    elif toolbar_order == "misaligned_x":
        # Next is before title
        close_b = "[30,108][138,216]"
        next_b = "[144,96][300,204]"
        title_b = "[310,96][810,228]"
        title_child_b = "[413,134][668,193]"
    elif toolbar_order == "misaligned_y":
        # Close is vertically far off
        close_b = "[30,400][138,500]"
        title_b = "[144,96][810,228]"
        title_child_b = "[413,134][668,193]"
        next_b = "[810,96][1068,204]"
    else:
        close_b = "[30,108][138,216]"
        title_b = "[144,96][810,228]"
        title_child_b = "[413,134][668,193]"
        next_b = "[810,96][1068,204]"

    nodes = []

    if has_close:
        nodes.append(
            f'<node package="{package_name}" class="android.widget.Button" '
            f'clickable="{str(close_clickable).lower()}" enabled="{str(close_enabled).lower()}" '
            f'bounds="{close_b}" content-desc="Close" text="" />'
        )

    # Title outer with nested duplicate label in same spatial region
    nodes.append(
        f'<node package="{package_name}" class="android.view.ViewGroup" '
        f'clickable="false" enabled="true" bounds="{title_b}" content-desc="{top_title_text}" text="">'
        f'<node package="{package_name}" class="android.widget.TextView" '
        f'clickable="false" enabled="true" bounds="{title_child_b}" content-desc="{top_title_text}" text="{top_title_text}" />'
        f'</node>'
    )

    if has_next:
        nodes.append(
            f'<node package="{package_name}" class="android.widget.Button" '
            f'clickable="false" enabled="false" bounds="{next_b}" content-desc="Next" text="">'
            f'<node package="{package_name}" class="android.view.ViewGroup" '
            f'clickable="false" enabled="false" bounds="[892,124][986,177]" content-desc="Next" text="Next" />'
            f'</node>'
        )

    if has_body:
        nodes.append(
            f'<node package="{package_name}" class="android.view.ViewGroup" '
            f'clickable="false" enabled="true" bounds="{body_bounds}" content-desc="{body_author_text}" text="{body_author_text}" />'
        )

    for i in range(caption_count):
        b = caption_bounds if i == 0 else "[0,850][1080,950]"
        nodes.append(
            f'<node package="{package_name}" class="android.widget.AutoCompleteTextView" '
            f'clickable="true" enabled="true" bounds="{b}" content-desc="" text="{caption_text}" />'
        )

    inner = "".join(nodes)
    return f'<hierarchy rotation="0" bounds="[0,0][1080,2408]">{inner}</hierarchy>'


def test_actor_title_variant_success():
    xml = build_measured_actor_title_xml(author="OwnPageTarget")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is True
    assert err == ""
    assert readiness["has_header"] is True
    assert readiness["has_close"] is True
    assert readiness["author_matched"] is True
    # uiautomator reports the hint as text: the placeholder means an empty composer (seen on the real phone).
    assert readiness["empty_state_certainty"] is True


def test_actor_title_variant_existing_draft_text_is_not_empty():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", caption_text="Old draft text")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is True
    assert readiness["empty_state_certainty"] is False


def test_actor_title_variant_abbreviated_activity_success():
    xml = build_measured_actor_title_xml(author="OwnPageTarget")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP_SHORT)
    assert ok is True
    assert err == ""
    assert readiness["has_header"] is True


def test_actor_title_variant_empty_caption_certainty_true():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", caption_text="")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is True
    assert readiness["empty_state_certainty"] is True


def test_actor_title_variant_rejected_without_window_dump():
    xml = build_measured_actor_title_xml(author="OwnPageTarget")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=None)
    assert ok is False
    assert "header" in err.lower()


def test_actor_title_variant_rejected_wrong_activity():
    xml = build_measured_actor_title_xml(author="OwnPageTarget")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=WRONG_ACTIVITY_WINDOW_DUMP)
    assert ok is False
    assert "activity" in err.lower() or "composeractivity" in err.lower()


def test_actor_title_variant_rejected_contradictory_app():
    xml = build_measured_actor_title_xml(author="OwnPageTarget")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=CONTRADICTORY_APP_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_wrong_top_title():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", top_title_text="WrongTitle")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_wrong_body_author():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", body_author_text="OtherAuthor")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_missing_body():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", has_body=False)
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_missing_close():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", has_close=False)
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False
    assert "close" in err.lower()


def test_actor_title_variant_rejected_nonactionable_close():
    # close not clickable
    xml = build_measured_actor_title_xml(author="OwnPageTarget", close_clickable=False, close_enabled=True)
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False

    # close not enabled
    xml2 = build_measured_actor_title_xml(author="OwnPageTarget", close_clickable=True, close_enabled=False)
    tree2 = ET.fromstring(xml2)
    ok2, err2, readiness2 = verify_composer_binding(tree2, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok2 is False


def test_actor_title_variant_rejected_missing_next():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", has_next=False)
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False
    assert "next" in err.lower()


def test_actor_title_variant_rejected_misaligned_toolbar_x():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", toolbar_order="misaligned_x")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_misaligned_toolbar_y():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", toolbar_order="misaligned_y")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_body_below_input():
    # input at [0,690][1080,792], body below it at [252,800][529,860]
    xml = build_measured_actor_title_xml(author="OwnPageTarget", body_bounds="[252,800][529,860]")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_body_overlaps_title():
    # body in toolbar region
    xml = build_measured_actor_title_xml(author="OwnPageTarget", body_bounds="[150,100][400,200]")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_actor_title_variant_rejected_multiple_caption_inputs():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", caption_count=2)
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False
    assert "input" in err.lower()


def test_actor_title_variant_rejected_wrong_package():
    xml = build_measured_actor_title_xml(author="OwnPageTarget", package_name="com.other.app")
    tree = ET.fromstring(xml)
    ok, err, readiness = verify_composer_binding(tree, "OwnPageTarget", window_dump=COMPOSER_WINDOW_DUMP)
    assert ok is False


def test_legacy_new_post_header_success_unchanged():
    legacy_xml = (
        '<hierarchy rotation="0">'
        '<node package="com.facebook.katana" class="android.widget.TextView" text="New post" bounds="[100,50][300,100]" />'
        '<node package="com.facebook.katana" class="android.widget.Button" content-desc="Close" bounds="[30,50][90,100]" />'
        '<node package="com.facebook.katana" class="android.widget.TextView" text="LegacyAuthor" bounds="[100,150][300,200]" />'
        '<node package="com.facebook.katana" class="android.widget.EditText" bounds="[50,300][1000,500]" text="" />'
        '</hierarchy>'
    )
    tree = ET.fromstring(legacy_xml)
    ok, err, readiness = verify_composer_binding(tree, "LegacyAuthor", window_dump=None)
    assert ok is True
    assert readiness["has_header"] is True
    assert readiness["has_close"] is True
    assert readiness["author_matched"] is True
    assert readiness["empty_state_certainty"] is True


TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_NAME = "OwnRootUser"
TEST_ROOT_ID = "100012345678901"


def _setup_collector_env(monkeypatch, window_dumps, ui_dumps):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.identity_proof.query_power_state", lambda s: AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", ""))
    monkeypatch.setattr("phone_runner.identity_proof.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.identity_proof.query_activity_state", lambda s: AdbResult(0, "top-activity: com.facebook.katana/.FeedActivity", ""))
    monkeypatch.setattr("phone_runner.identity_proof._try_open_own_root_without_model", lambda *a, **kw: False)
    adb_rec = AdbCommandRecorder()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", adb_rec)
    monkeypatch.setattr("phone_runner.identity_proof.run_mobilerun", lambda **kw: MobilerunResult(0, "Navigated", ""))

    w_iter = iter(window_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_window_state", lambda s: AdbResult(0, next(w_iter, window_dumps[-1]), ""))

    u_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.identity_proof.query_ui_hierarchy", lambda s: AdbResult(0, next(u_iter), ""))

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


def test_collector_step6_actor_title_success(monkeypatch):
    tab_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=... com.facebook.katana\n"
    )
    window_dumps = [
        tab_dump,  # initial verify_foreground
        tab_dump,  # root profile
        tab_dump,  # root settings
        tab_dump,  # return to root profile
        COMPOSER_WINDOW_DUMP,  # Step 6 fresh query_window_state
    ]
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        build_measured_actor_title_xml(author=TEST_ROOT_NAME),
    ]
    _setup_collector_env(monkeypatch, window_dumps, ui_dumps)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )
    assert proof.passed is True
    assert proof.active_composer_actor_id == TEST_ROOT_ID
    assert proof.composer_readiness["has_header"] is True
    assert proof.composer_readiness["header_variant"] == "actor_title"


def test_collector_step6_actor_title_rejected_rotation_nonzero(monkeypatch):
    tab_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=... com.facebook.katana\n"
    )
    rot_dump = (
        "mCurrentRotation=ROTATION_90\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.composer.activity.ComposerActivity}\n"
    )
    window_dumps = [
        tab_dump,  # 1: initial verify_foreground
        tab_dump,  # 2: root profile
        tab_dump,  # 3: root settings
        tab_dump,  # 4: return to root profile
        COMPOSER_WINDOW_DUMP,  # 5: composer verify_foreground_facebook
        rot_dump,  # 6: Step 6 fresh query_window_state with rotation 90
    ]
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        build_measured_actor_title_xml(author=TEST_ROOT_NAME),
    ]
    _setup_collector_env(monkeypatch, window_dumps, ui_dumps)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )
    assert proof.passed is False
    assert proof.active_composer_actor_id is None
    assert "rotation" in proof.stages[-1]["detail"].lower()


def test_collector_step6_actor_title_rejected_wrong_activity(monkeypatch):
    tab_dump = (
        "mCurrentRotation=ROTATION_0\n"
        "mCurrentFocus=Window{605df14 u0 com.facebook.katana/com.facebook.katana.activity.FbMainTabActivity}\n"
        "mFocusedApp=... com.facebook.katana\n"
    )
    window_dumps = [
        tab_dump,  # 1: initial verify_foreground
        tab_dump,  # 2: root profile
        tab_dump,  # 3: root settings
        tab_dump,  # 4: return to root profile
        COMPOSER_WINDOW_DUMP,  # 5: composer verify_foreground_facebook
        WRONG_ACTIVITY_WINDOW_DUMP,  # 6: Step 6 fresh query_window_state with FbMainTabActivity
    ]
    ui_dumps = [
        ROOT_PROFILE_XML,
        ROOT_SETTINGS_XML,
        ROOT_PROFILE_XML,
        build_measured_actor_title_xml(author=TEST_ROOT_NAME),
    ]
    _setup_collector_env(monkeypatch, window_dumps, ui_dumps)

    proof = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name=TEST_ROOT_NAME,
        expected_root_id=TEST_ROOT_ID,
    )
    assert proof.passed is False
    assert proof.active_composer_actor_id is None
    assert "composeractivity" in proof.stages[-1]["detail"].lower()
