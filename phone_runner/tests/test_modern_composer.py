import pytest
from unittest import mock

from phone_runner.adb import AdbResult
from phone_runner.modern_composer import (
    CAPTION_MATCH_PREFIX_CHARS,
    RESOLVE_ATTEMPTS,
    RESOLVE_RETRY_WAIT_SECONDS,
    attach_first_gallery_item,
    caption_match_prefix,
    composer_state,
    discard_composer,
    extract_post_id_from_url,
    find_matching_post_more_options,
    has_attachment_preview,
    is_empty_composer,
    normalize_feed_text,
    open_share_intent_composer,
    reel_settings_state,
    resolve_latest_post_link,
    share_screen_state,
)
from phone_runner.publish import execute_publish


class _NoopImeContext:
    def __init__(self, serial):
        self.serial = serial

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _no_real_portal_ime(monkeypatch):
    monkeypatch.setattr("phone_runner.modern_composer.PortalImeContext", _NoopImeContext)


TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100009439229483"
TEST_ROOT_NAME = "Agung"
TEST_DEST_ID = "100009439229483"
TEST_DEST_NAME = "Agung"

EMPTY_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[30,108][138,216]" content-desc="Close" />
    <node class="android.view.ViewGroup" bounds="[144,96][810,228]" text="Agung" content-desc="Agung" />
    <node class="android.widget.Button" clickable="true" bounds="[810,96][1068,204]" content-desc="Next" enabled="false" />
    <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="Agung" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[0,690][1080,792]" text="What's on your mind?" />
    <node class="android.widget.Button" clickable="true" bounds="[72,1379][204,1511]" content-desc="Gallery" />
  </node>
</hierarchy>"""

DRAFT_NEXT_ENABLED_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[30,108][138,216]" content-desc="Close" />
    <node class="android.view.ViewGroup" bounds="[144,96][810,228]" text="Agung" content-desc="Agung" />
    <node class="android.widget.Button" clickable="true" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
    <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="Agung" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[0,690][1080,792]" text="What's on your mind?" />
    <node class="android.widget.Button" clickable="true" bounds="[72,1379][204,1511]" content-desc="Gallery" />
  </node>
</hierarchy>"""

WRONG_AUTHOR_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[30,108][138,216]" content-desc="Close" />
    <node class="android.view.ViewGroup" bounds="[144,96][810,228]" text="GP Weekend" content-desc="GP Weekend" />
    <node class="android.widget.Button" clickable="true" bounds="[810,96][1068,204]" content-desc="Next" enabled="false" />
    <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="GP Weekend" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[0,690][1080,792]" text="What's on your mind?" />
    <node class="android.widget.Button" clickable="true" bounds="[72,1379][204,1511]" content-desc="Gallery" />
  </node>
</hierarchy>"""

TYPED_COMPOSER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[30,108][138,216]" content-desc="Close" />
    <node class="android.view.ViewGroup" bounds="[144,96][810,228]" text="Agung" content-desc="Agung" />
    <node class="android.widget.Button" clickable="true" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
    <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="Agung" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[0,690][1080,792]" text="Hello modern world" />
    <node class="android.widget.Button" clickable="true" bounds="[72,1379][204,1511]" content-desc="Gallery" />
  </node>
</hierarchy>"""

POST_SETTINGS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Post settings" bounds="[200,100][600,200]" />
    <node class="android.widget.Button" content-desc="SHARE" bounds="[811,125][1026,233]" />
    <node class="android.widget.Button" content-desc="Post audience, Public" bounds="[50,300][1030,450]" />
  </node>
</hierarchy>"""

POST_SETTINGS_NO_SHARE_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Post settings" bounds="[200,100][600,200]" />
    <node class="android.widget.Button" content-desc="Post audience, Public" bounds="[50,300][1030,450]" />
  </node>
</hierarchy>"""

GALLERY_PICKER_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="New post" bounds="[100,100][400,200]" />
    <node class="android.view.View" clickable="true" content-desc="Photo, item 1, taken on Oct 16" bounds="[0,300][360,660]" />
  </node>
</hierarchy>"""

COMPOSER_WITH_PREVIEW_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[30,108][138,216]" content-desc="Close" />
    <node class="android.view.ViewGroup" bounds="[144,96][810,228]" text="Agung" content-desc="Agung" />
    <node class="android.widget.Button" clickable="true" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
    <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="Agung" />
    <node class="android.widget.AutoCompleteTextView" clickable="true" bounds="[0,690][1080,792]" text="Hello with photo" />
    <node class="android.widget.ImageView" resource-id="com.facebook.katana:id/media_attachment" content-desc="photo attachment" bounds="[50,800][500,1200]" />
  </node>
</hierarchy>"""

DISCARD_DIALOG_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" text="Save as draft" bounds="[100,800][980,900]" />
    <node class="android.widget.Button" text="Discard post" bounds="[100,950][980,1050]" />
    <node class="android.widget.Button" text="Continue editing" bounds="[100,1100][980,1200]" />
  </node>
</hierarchy>"""

PROFILE_SCREEN_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[950,500][1050,600]" />
  </node>
</hierarchy>"""

POST_OPTIONS_XML = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Copy link" bounds="[100,1500][900,1600]" />
  </node>
</hierarchy>"""

FAKE_PROBE_READY = {
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


@pytest.fixture(autouse=True)


def _common_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "5242880")
    monkeypatch.setattr(
        "phone_runner.publish.ping_portal",
        lambda s: AdbResult(0, "Row: 0 result=pong\n", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.wake_and_unlock",
        lambda s: AdbResult(0, "OK", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.is_device_secure_locked",
        lambda s: False,
    )


def _make_valid_proof(empty_state_certainty=True):
    from phone_runner.identity_proof import IdentityProofResult

    return IdentityProofResult(
        nonce="test_nonce_modern",
        created_at="2026-10-16T12:00:00Z",
        serial=TEST_SERIAL,
        method="own_url_chain_v1",
        evidence_source="adb_device_read",
        observed_root_id=TEST_ROOT_ID,
        observed_root_url=f"https://www.facebook.com/profile.php?id={TEST_ROOT_ID}",
        observed_target_id=TEST_DEST_ID,
        observed_target_url=f"https://www.facebook.com/profile.php?id={TEST_DEST_ID}",
        verified_main_profile_id=TEST_ROOT_ID,
        active_composer_actor_id=TEST_DEST_ID,
        app_card_version="1.0.0",
        facebook_version="580.0.0.51.74",
        portal_version="0.7.25",
        resolution="1080x2408",
        rotation=0,
        steps=[],
        passed=True,
        stages=[],
        composer_readiness={
            "has_header": True,
            "has_close": True,
            "input_class": "android.widget.AutoCompleteTextView",
            "author_matched": True,
            "empty_state_certainty": empty_state_certainty,
            "header_variant": "actor_title",
        },
    )


def test_composer_state_and_is_empty_composer_empty():
    state = composer_state(EMPTY_COMPOSER_XML)
    assert state["in_composer"] is True
    assert state["author_names"] == ["Agung"]
    assert state["next_enabled"] is False
    assert state["next_bounds"] == (810, 96, 1068, 204)
    assert state["input_bounds"] == (0, 690, 1080, 792)
    assert state["gallery_bounds"] == (72, 1379, 204, 1511)
    assert state["input_text"] == "What's on your mind?"

    ok, reason = is_empty_composer(state, "Agung")
    assert ok is True
    assert "empty composer verified" in reason


def test_is_empty_composer_next_enabled_fails_draft():
    state = composer_state(DRAFT_NEXT_ENABLED_COMPOSER_XML)
    assert state["in_composer"] is True
    assert state["next_enabled"] is True

    ok, reason = is_empty_composer(state, "Agung")
    assert ok is False
    assert "draft present" in reason


def test_is_empty_composer_wrong_author_fails():
    state = composer_state(WRONG_AUTHOR_COMPOSER_XML)
    assert state["author_names"] == ["GP Weekend"]

    ok, reason = is_empty_composer(state, "Agung")
    assert ok is False
    assert "wrong author" in reason


def test_composer_state_typed_text():
    state = composer_state(TYPED_COMPOSER_XML)
    assert state["input_text"] == "Hello modern world"
    assert state["next_enabled"] is True

    ok, reason = is_empty_composer(state, "Agung")
    assert ok is False
    assert "draft present" in reason


def test_share_screen_state_on_post_settings_fixture():
    state = share_screen_state(POST_SETTINGS_XML)
    assert state["is_post_settings"] is True
    assert state["share_bounds"] == (811, 125, 1026, 233)
    assert state["audience"] == "Public"


def test_share_screen_state_no_share_button():
    state = share_screen_state(POST_SETTINGS_NO_SHARE_XML)
    assert state["is_post_settings"] is False
    assert state["share_bounds"] is None


def test_extract_post_id_from_url():
    # Numeric post id in path
    pid, p_url = extract_post_id_from_url("https://www.facebook.com/100009439229483/posts/1234567890123/?mibextid=123")
    assert pid == "1234567890123"
    assert p_url == "https://www.facebook.com/100009439229483/posts/1234567890123/"

    # Pfbid token in path
    pid2, p_url2 = extract_post_id_from_url("https://www.facebook.com/username/posts/pfbid02rX8xyz987")
    assert pid2 == "pfbid02rX8xyz987"
    assert p_url2 == "https://www.facebook.com/username/posts/pfbid02rX8xyz987"

    # Story fbid query param
    pid3, p_url3 = extract_post_id_from_url("https://www.facebook.com/permalink.php?story_fbid=pfbid0AbC&id=100009439229483")
    assert pid3 == "pfbid0AbC"
    assert "story_fbid=pfbid0AbC" in p_url3


def test_discard_composer_taps_discard_never_save_draft(monkeypatch):
    taps = []
    keyevents = []

    def mock_run_adb(args, serial=None):
        if "keyevent" in args:
            keyevents.append(args[-1])
        if "tap" in args:
            taps.append((int(args[-2]), int(args[-1])))
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.modern_composer.run_adb", mock_run_adb)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr(
        "phone_runner.modern_composer.query_ui_hierarchy",
        lambda s: AdbResult(0, DISCARD_DIALOG_XML, ""),
    )

    discard_composer(TEST_SERIAL)
    assert "4" in keyevents
    # Center of [100,950][980,1050] is (540, 1000)
    assert (540, 1000) in taps
    # Center of Save as draft [100,800][980,900] is (540, 850) -> MUST NOT BE TAPPED!
    assert (540, 850) not in taps


def test_execute_publish_modern_text_only_published(monkeypatch):
    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    # UI sequence:
    # 1. candidate capture -> no direct IDs
    # 2. Step a: dump empty composer
    # 3. Step c: dump typed composer
    # 4. Step d: dump post settings
    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, TYPED_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    taps = []
    def mock_human_tap(serial, bounds, rng=None):
        taps.append(bounds)

    monkeypatch.setattr("phone_runner.publish.human_tap", mock_human_tap)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))
    from phone_runner.mobilerun import MobilerunResult
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    resolved_post_id = "pfbid02rX8abc"
    resolved_permalink = f"https://www.facebook.com/{TEST_ROOT_ID}/posts/{resolved_post_id}"
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: (resolved_post_id, resolved_permalink, "verified via top post"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == resolved_post_id
    assert res["permalink"] == resolved_permalink
    assert res["publish_may_have_started"] is True
    assert res["error_code"] is None

    # Verify SHARE button [811,125][1026,233] was tapped exactly once
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 1


def test_execute_publish_modern_text_only_uncertain_when_resolve_fails(monkeypatch):
    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, TYPED_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    taps = []
    def mock_human_tap(serial, bounds, rng=None):
        taps.append(bounds)

    monkeypatch.setattr("phone_runner.publish.human_tap", mock_human_tap)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))
    from phone_runner.mobilerun import MobilerunResult
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    # Resolve fails
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: (None, None, "More options button not found"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
    )

    assert res["status"] == "uncertain"
    assert res["facebook_post_id"] is None
    assert res["publish_may_have_started"] is True
    assert res["error_code"] == "post_identity_unproven"

    # SHARE tapped exactly once
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 1


def test_execute_publish_modern_draft_present_fails_draft_state_uncertain(monkeypatch):
    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    # Next enabled in composer -> draft present!
    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, DRAFT_NEXT_ENABLED_COMPOSER_XML, ""),
        AdbResult(0, DISCARD_DIALOG_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    taps = []
    def mock_run_adb(args, serial=None):
        if "tap" in args:
            taps.append((int(args[-2]), int(args[-1])))
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.publish.run_adb", mock_run_adb)
    mock_type = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.type_text", mock_type)
    mock_obs = mock.Mock(return_value="/obs/draft.json")
    monkeypatch.setattr("phone_runner.publish.save_composer_observation", mock_obs)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "draft_state_uncertain"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    mock_obs.assert_called_once_with(TEST_SERIAL, "draft_state_uncertain")

    # SHARE never tapped
    share_taps = [t for t in taps if t == (918, 179)]
    assert len(share_taps) == 0


def test_execute_publish_modern_caption_mismatch_fails_and_discards(monkeypatch):
    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    # After typing, input text is different from expected
    mismatch_typed_xml = TYPED_COMPOSER_XML.replace("Hello modern world", "Different typed text")
    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, mismatch_typed_xml, ""),
        AdbResult(0, DISCARD_DIALOG_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    taps = []
    def mock_run_adb(args, serial=None):
        if "tap" in args:
            taps.append((int(args[-2]), int(args[-1])))
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.publish.run_adb", mock_run_adb)
    from phone_runner.mobilerun import MobilerunResult
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    mock_obs = mock.Mock(return_value="/obs/mismatch.json")
    monkeypatch.setattr("phone_runner.publish.save_composer_observation", mock_obs)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello modern world",
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "caption_mismatch"
    assert res["publish_may_have_started"] is False
    mock_obs.assert_called_once_with(TEST_SERIAL, "caption_mismatch")

    # SHARE never tapped
    share_taps = [t for t in taps if t == (918, 179)]
    assert len(share_taps) == 0


def test_execute_publish_modern_media_attach_failure_fails_and_discards(monkeypatch):
    proof = _make_valid_proof(empty_state_certainty=True)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    ui_dumps = [
        AdbResult(0, "<hierarchy><node text='Feed' /></hierarchy>", ""),
        AdbResult(0, EMPTY_COMPOSER_XML, ""),
        AdbResult(0, DISCARD_DIALOG_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    taps = []
    def mock_run_adb(args, serial=None):
        if "tap" in args:
            taps.append((int(args[-2]), int(args[-1])))
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.publish.run_adb", mock_run_adb)
    mock_type = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.type_text", mock_type)

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/pic.jpg", "url": "https://cdn.example.com/p.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)

    # attach_first_gallery_item fails
    monkeypatch.setattr(
        "phone_runner.publish.attach_first_gallery_item",
        lambda s: (False, "gallery screen 'New post' header not found"),
    )
    mock_obs = mock.Mock(return_value="/obs/attach_fail.json")
    monkeypatch.setattr("phone_runner.publish.save_composer_observation", mock_obs)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with photo",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "media_attach_failed"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    mock_obs.assert_called_once_with(TEST_SERIAL, "media_attach_failed")

    # SHARE never tapped
    share_taps = [t for t in taps if t == (918, 179)]
    assert len(share_taps) == 0


def test_normalize_feed_text_and_caption_match_prefix():
    # 5a: collapses "A\n\n  B C" -> "A B C" and unescapes "&amp;" -> "&"
    raw = "A\n\n  B\u00a0C"
    assert normalize_feed_text(raw) == "A B C"
    assert normalize_feed_text("&amp;") == "&"
    assert normalize_feed_text("Tom &amp; Jerry\n\t  News") == "Tom & Jerry News"
    assert normalize_feed_text(None) == ""
    assert normalize_feed_text("") == ""

    # prefix
    assert caption_match_prefix(None) == ""
    assert caption_match_prefix("   ") == ""
    long_caption = "A" * 50
    assert caption_match_prefix(long_caption) == "A" * 30
    assert len(caption_match_prefix(long_caption)) == 30
    assert CAPTION_MATCH_PREFIX_CHARS == 30
    assert RESOLVE_ATTEMPTS == 4
    assert RESOLVE_RETRY_WAIT_SECONDS == 10.0


def test_find_matching_post_more_options_picks_nearest_above():
    # 5b: caption node at y=900 with More options at y=700 (our post) and another More options at y=300 (older post above) -> returns the y=700 bounds
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[900,300][1000,400]" />
    <node class="android.view.ViewGroup" text="Older post caption" bounds="[100,450][1000,550]" />
    <node class="android.widget.Button" content-desc="More options" bounds="[900,700][1000,800]" />
    <node class="android.view.ViewGroup" text="Our target caption prefix match here" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""
    bounds, detail = find_matching_post_more_options(xml, "Our target caption prefix match")
    assert bounds == (900, 700, 1000, 800)
    assert "matched caption prefix at y=900" in detail


def test_find_matching_post_more_options_no_caption_match():
    # 5c: More options exists but no caption match -> None
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[900,700][1000,800]" />
    <node class="android.view.ViewGroup" text="Completely unrelated post" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""
    bounds, detail = find_matching_post_more_options(xml, "Target caption looking for")
    assert bounds is None
    assert "no post with matching caption visible" in detail


def test_find_matching_post_more_options_collapsed_newlines_match():
    # 5d: caption text in the hierarchy is the caption with newlines collapsed differently (caption has "\n", feed shows a space) -> still matches
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[900,700][1000,800]" />
    <node class="android.view.ViewGroup" text="Breaking news: Championship results are officially announced" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""
    caption = "Breaking news:\nChampionship results\nare officially announced"
    bounds, detail = find_matching_post_more_options(xml, caption)
    assert bounds == (900, 700, 1000, 800)
    assert "matched caption prefix at y=900" in detail


def test_find_matching_post_more_options_empty_caption():
    # 5e: empty caption -> None with "empty caption"
    xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[900,700][1000,800]" />
  </node>
</hierarchy>"""
    bounds, detail = find_matching_post_more_options(xml, "")
    assert bounds is None
    assert "empty caption" in detail

    bounds_none, detail_none = find_matching_post_more_options(xml, None)
    assert bounds_none is None
    assert "empty caption" in detail_none


def test_resolve_latest_post_link_matches_on_third_attempt(monkeypatch):
    monkeypatch.setattr("phone_runner.modern_composer.PROFILE_POST_SEARCH_SWIPES", 1)  # one swipe per attempt in this scenario
    # 5f: first 2 attempts no match, 3rd attempt match -> returns the id parsed from the mocked clipboard
    # "https://www.facebook.com/agung/posts/pfbid0abcDEF123"; assert More options was tapped exactly once.
    no_match_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" text="Older irrelevant post" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""

    match_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[900,700][1000,800]" />
    <node class="android.view.ViewGroup" text="My target post caption here" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""

    post_options_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Copy link" bounds="[100,1500][900,1600]" />
  </node>
</hierarchy>"""

    dumps = [
        AdbResult(0, no_match_xml, ""),
        AdbResult(0, no_match_xml, ""),
        AdbResult(0, no_match_xml, ""),
        AdbResult(0, no_match_xml, ""),
        AdbResult(0, match_xml, ""),
        AdbResult(0, post_options_xml, ""),
    ]
    dump_iter = iter(dumps)

    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_999"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: next(dump_iter))
    monkeypatch.setattr(
        "phone_runner.modern_composer.read_device_clipboard",
        lambda s: AdbResult(0, "https://www.facebook.com/agung/posts/pfbid0abcDEF123", ""),
    )

    taps = []
    swipes = []
    def mock_human_tap(serial, bounds, rng=None):
        taps.append(bounds)

    def mock_run_adb(args, serial=None):
        if "swipe" in args:
            swipes.append(args)
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.modern_composer.human_tap", mock_human_tap)
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", mock_run_adb)

    pid, permalink, detail = resolve_latest_post_link(TEST_SERIAL, "My target post caption here")

    assert pid == "pfbid0abcDEF123"
    assert permalink == "https://www.facebook.com/agung/posts/pfbid0abcDEF123"
    assert "resolved post pfbid0abcDEF123" in detail

    # More options [900,700][1000,800]
    more_options_taps = [b for b in taps if b == (900, 700, 1000, 800)]
    assert len(more_options_taps) == 1
    # Copy link [100,1500][900,1600]
    copy_link_taps = [b for b in taps if b == (100, 1500, 900, 1600)]
    assert len(copy_link_taps) == 1
    # Swipes: 2 swipes total (one in attempt 1, one in attempt 2)
    assert len(swipes) == 2


def test_resolve_latest_post_link_never_matches(monkeypatch):
    monkeypatch.setattr("phone_runner.modern_composer.PROFILE_POST_SEARCH_SWIPES", 1)  # one swipe per attempt in this scenario
    # 5g: never matches -> (None, None, detail containing "not found on own profile after 4 attempts");
    # assert no tap other than swipes happened.
    no_match_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.view.ViewGroup" text="Other post" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""

    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_999"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: AdbResult(0, no_match_xml, ""))

    taps = []
    swipes = []
    def mock_run_adb(args, serial=None):
        if "tap" in args:
            taps.append((int(args[-2]), int(args[-1])))
        elif "swipe" in args:
            swipes.append(args)
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.modern_composer.run_adb", mock_run_adb)

    pid, permalink, detail = resolve_latest_post_link(TEST_SERIAL, "Looking for my post")

    assert pid is None
    assert permalink is None
    assert "not found on own profile after 4 attempts" in detail
    assert len(taps) == 0
    # 4 attempts * 1 swipe per attempt = 4 swipes
    assert len(swipes) == 4


def test_resolve_latest_post_link_copy_link_not_found(monkeypatch):
    match_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options" bounds="[900,700][1000,800]" />
    <node class="android.view.ViewGroup" text="My target post caption here" bounds="[100,900][1000,1000]" />
  </node>
</hierarchy>"""

    no_copy_link_options_xml = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Save post" bounds="[100,1500][900,1600]" />
  </node>
</hierarchy>"""

    dumps = [
        AdbResult(0, match_xml, ""),
        AdbResult(0, no_copy_link_options_xml, ""),  # first look
        AdbResult(0, no_copy_link_options_xml, ""),  # re-check after the sheet settles
        AdbResult(0, no_copy_link_options_xml, ""),  # after scrolling the sheet once
    ]
    dump_iter = iter(dumps)

    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_999"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: next(dump_iter))

    keyevents = []
    taps = []
    def mock_human_tap(serial, bounds, rng=None):
        taps.append(bounds)

    def mock_run_adb(args, serial=None):
        if "keyevent" in args:
            keyevents.append(args[-1])
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.modern_composer.human_tap", mock_human_tap)
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", mock_run_adb)

    pid, permalink, detail = resolve_latest_post_link(TEST_SERIAL, "My target post caption here")

    assert pid is None
    assert permalink is None
    assert "'Copy link' option not found" in detail
    assert "4" in keyevents
    # More options was tapped once, Copy link was not tapped
    assert len(taps) == 1
    assert taps[0] == (900, 700, 1000, 800)


def test_has_attachment_preview_photo_and_remove_photo():
    xml = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
        <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
      </node>
    </hierarchy>"""
    assert has_attachment_preview(xml) is True

    # Missing "Remove photo"
    xml_no_remove = """<hierarchy rotation="0">
      <node package="com.facebook.katana">
        <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
      </node>
    </hierarchy>"""
    assert has_attachment_preview(xml_no_remove) is False


def test_discard_composer_labels(monkeypatch):
    labels = ["Discard post", "DISCARD", "Discard"]
    for lbl in labels:
        xml = f"""<hierarchy rotation="0">
          <node class="android.widget.Button" text="{lbl}" bounds="[100,950][980,1050]" />
        </hierarchy>"""
        taps = []
        monkeypatch.setattr(
            "phone_runner.modern_composer.query_ui_hierarchy",
            lambda s: AdbResult(0, xml, ""),
        )
        monkeypatch.setattr(
            "phone_runner.modern_composer.run_adb",
            lambda args, serial=None: taps.append(args) if "tap" in args else AdbResult(0, "OK", ""),
        )
        discard_composer(TEST_SERIAL)
        assert len(taps) == 1
        assert taps[0][-2:] == ["540", "1000"]


def test_open_share_intent_composer_success(monkeypatch):
    share_composer_xml = """<hierarchy rotation="0">
      <node class="android.widget.Button" content-desc="Close" bounds="[30,108][138,216]" />
      <node class="android.widget.Button" content-desc="Next" bounds="[810,96][1068,204]" enabled="true" />
      <node class="android.view.ViewGroup" text="Agung" bounds="[252,325][424,384]" />
      <node class="android.widget.AutoCompleteTextView" text="What's on your mind?" bounds="[0,690][1080,792]" />
      <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
      <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
    </hierarchy>"""

    adb_cmds = []
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda args, serial=None: adb_cmds.append(args) or AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.modern_composer.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: AdbResult(0, share_composer_xml, ""))

    ok, detail, xml = open_share_intent_composer(TEST_SERIAL, "content://media/external/images/media/123", "Agung")
    assert ok is True
    assert "verified" in detail
    assert xml == share_composer_xml
    # Verify am start command was executed
    assert any("android.intent.action.SEND" in c for c in adb_cmds)


def test_open_share_intent_composer_wrong_author(monkeypatch):
    wrong_auth_xml = """<hierarchy rotation="0">
      <node class="android.widget.Button" content-desc="Close" bounds="[30,108][138,216]" />
      <node class="android.widget.Button" content-desc="Next" bounds="[810,96][1068,204]" enabled="true" />
      <node class="android.view.ViewGroup" text="Agung Baelah" bounds="[252,325][424,384]" />
      <node class="android.widget.AutoCompleteTextView" text="What's on your mind?" bounds="[0,690][1080,792]" />
      <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
      <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
    </hierarchy>"""

    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.modern_composer.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: AdbResult(0, wrong_auth_xml, ""))
    discard_mock = mock.Mock()
    monkeypatch.setattr("phone_runner.modern_composer.discard_composer", discard_mock)

    ok, detail, xml = open_share_intent_composer(TEST_SERIAL, "content://media/external/images/media/123", "Agung")
    assert ok is False
    assert "wrong author" in detail
    discard_mock.assert_called_once()



def test_resolve_returns_unproven_when_portal_keyboard_unavailable(monkeypatch):
    class _Broken:
        def __init__(self, serial):
            pass

        def __enter__(self):
            raise RuntimeError("Failed to query default IME")

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr("phone_runner.modern_composer.PortalImeContext", _Broken)
    post_id, permalink, detail = resolve_latest_post_link("100.64.96.50:5555", "caption")
    assert post_id is None and permalink is None
    assert "portal keyboard unavailable" in detail


def test_post_url_allowlist_accepts_post_links_only():
    from phone_runner.resolver import is_allowed_facebook_post_url, is_allowed_facebook_url

    good = [
        "https://www.facebook.com/share/p/1MajvEXHB3/",
        "https://www.facebook.com/share/v/AbC123/",
        "https://www.facebook.com/story.php?story_fbid=4727735434217717&id=100009439229483",
    ]
    bad = [
        "https://evil.com/share/p/1MajvEXHB3/",
        "https://www.facebook.com/share/p/1Maj/?next=https://evil.com",
        "http://www.facebook.com/share/p/1MajvEXHB3/",
        "https://www.facebook.com/story.php?story_fbid=abc&id=1",
    ]
    assert all(is_allowed_facebook_post_url(u) for u in good)
    assert not any(is_allowed_facebook_post_url(u) for u in bad)
    # the identity (profile) allowlist stays strict
    assert not is_allowed_facebook_url("https://www.facebook.com/share/p/1MajvEXHB3/")


def test_has_attachment_preview_video():
    video_xml = """<hierarchy rotation="0">
      <node class="android.widget.Button" content-desc="Video" bounds="[0,891][1080,2104]" />
      <node class="android.widget.Button" content-desc="Remove video" bounds="[800,891][1000,1000]" />
    </hierarchy>"""
    assert has_attachment_preview(video_xml) is True

    video_only = """<hierarchy rotation="0">
      <node class="android.widget.Button" content-desc="Video" bounds="[0,891][1080,2104]" />
    </hierarchy>"""
    assert has_attachment_preview(video_only) is False


def test_reel_settings_state_parsing():
    fact4_xml = """<hierarchy rotation="0">
      <node class="android.view.ViewGroup" text="Reel Settings" bounds="[0,0][1080,216]" />
      <node class="android.widget.Button" content-desc="Back" bounds="[30,108][138,216]" />
      <node class="android.widget.AutoCompleteTextView" bounds="[330,276][1080,696]" text="My epic reel caption" content-desc="Describe your reel. You can also add hashtags here." />
      <node class="android.widget.Button" content-desc="Edit cover" bounds="[30,276][300,696]" />
      <node class="android.widget.Button" clickable="true" content-desc="Share now" bounds="[48,2096][1032,2228]" />
    </hierarchy>"""
    state = reel_settings_state(fact4_xml)
    assert state["is_reel_settings"] is True
    assert state["share_now_bounds"] == (48, 2096, 1032, 2228)
    assert state["caption_text"] == "My epic reel caption"

    # Mismatched/missing title
    no_title_xml = """<hierarchy rotation="0">
      <node class="android.widget.Button" clickable="true" content-desc="Share now" bounds="[48,2096][1032,2228]" />
    </hierarchy>"""
    assert reel_settings_state(no_title_xml)["is_reel_settings"] is False

    # Empty / malformed
    assert reel_settings_state("")["is_reel_settings"] is False


def test_resolve_latest_post_link_uses_custom_attempts(monkeypatch):
    sleep_calls = []
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: sleep_calls.append(s))
    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_xyz"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: AdbResult(0, "<hierarchy />", ""))
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda *a, **k: AdbResult(0, "OK", ""))

    post_id, permalink, detail = resolve_latest_post_link(
        TEST_SERIAL,
        caption="test",
        attempts=3,
        wait=7.5,
    )
    assert post_id is None and permalink is None
    assert "after 3 attempts" in detail
    assert 7.5 in sleep_calls



def test_resolve_scrolls_long_reel_menu_to_find_copy_link(monkeypatch):
    match_xml = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options for Agung's post" bounds="[954,406][1080,531]" />
    <node class="android.view.ViewGroup" text="It spins at up to ten thousand rpm, driven by compressed air." bounds="[36,598][1044,731]" />
    </node></hierarchy>"""
    top_of_menu = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" text="Pin post" bounds="[36,324][1044,456]" /></node></hierarchy>"""
    bottom_of_menu = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" text="Copy link" bounds="[36,1953][1044,2085]" /></node></hierarchy>"""
    dump_iter = iter([AdbResult(0, match_xml, ""), AdbResult(0, top_of_menu, ""), AdbResult(0, top_of_menu, ""), AdbResult(0, bottom_of_menu, "")])
    cmds, taps = [], []
    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_1"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: next(dump_iter))
    monkeypatch.setattr("phone_runner.modern_composer.human_tap", lambda serial, bounds, rng=None: taps.append(bounds))
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.modern_composer.read_device_clipboard",
        lambda s: AdbResult(0, "https://www.facebook.com/story.php?story_fbid=4727818027542791&id=100009439229483", ""),
    )

    pid, permalink, _ = resolve_latest_post_link(TEST_SERIAL, "It spins at up to ten thousand rpm, driven by compressed air.")

    assert pid == "4727818027542791"
    assert (36, 1953, 1044, 2085) in taps
    assert ["shell", "input", "swipe", "540", "1900", "540", "900", "400"] in cmds


def test_resolve_scrolls_down_a_fresh_profile_until_the_post_appears(monkeypatch):
    top = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Personal details" bounds="[36,300][800,360]" /></node></hierarchy>"""
    post = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" content-desc="More options for Agung's post" bounds="[954,1901][1080,2026]" />
    <node class="android.view.ViewGroup" text="The 2026 Grand Prix car has a rear wing flap that tilts flat" bounds="[36,2093][1044,2226]" />
    </node></hierarchy>"""
    menu = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" text="Copy link" bounds="[36,1953][1044,2085]" /></node></hierarchy>"""
    dumps = iter([AdbResult(0, top, ""), AdbResult(0, top, ""), AdbResult(0, top, ""), AdbResult(0, post, ""), AdbResult(0, menu, "")])
    cmds, taps = [], []
    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_1"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: next(dumps))
    monkeypatch.setattr("phone_runner.modern_composer.human_tap", lambda serial, bounds, rng=None: taps.append(bounds))
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.modern_composer.read_device_clipboard",
        lambda s: AdbResult(0, "https://www.facebook.com/story.php?story_fbid=4728190000000000&id=100009439229483", ""),
    )

    pid, _, _ = resolve_latest_post_link(TEST_SERIAL, "The 2026 Grand Prix car has a rear wing flap that tilts flat")

    assert pid == "4728190000000000"
    assert len([c for c in cmds if "swipe" in c]) == 3
    assert (954, 1901, 1080, 2026) in taps


def test_resolve_stops_at_the_deadline_without_extra_attempts(monkeypatch):
    top = """<hierarchy rotation="0"><node package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" text="Personal details" bounds="[36,300][800,360]" /></node></hierarchy>"""
    clock = {"t": 1000.0}
    monkeypatch.setattr("phone_runner.modern_composer.time.monotonic", lambda: clock["t"])
    monkeypatch.setattr("phone_runner.modern_composer.time.sleep", lambda s: clock.__setitem__("t", clock["t"] + s))
    monkeypatch.setattr("phone_runner.modern_composer.clear_and_verify_clipboard", lambda s: (True, "sentinel_1"))
    monkeypatch.setattr("phone_runner.modern_composer.launch_facebook_own_profile", lambda s: None)
    monkeypatch.setattr("phone_runner.modern_composer.query_ui_hierarchy", lambda s: AdbResult(0, top, ""))
    monkeypatch.setattr("phone_runner.modern_composer.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    pid, permalink, detail = resolve_latest_post_link(TEST_SERIAL, "some caption text here", attempts=8, wait=15.0, deadline=1060.0)

    assert pid is None and permalink is None
    assert clock["t"] <= 1060.0 + 20.0  # never runs far past the deadline
    assert "time budget" in detail or "not found" in detail
