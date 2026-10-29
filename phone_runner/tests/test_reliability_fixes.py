"""Targeted tests for TASK C2 phone_runner reliability fixes.

Covers:
Fix 1: Keep real media staging error text in stage details.
Fix 2: Scroll profile feed to top when header is above screen.
Fix 3: Caption typing fallback runs with Portal keyboard context.
"""
from datetime import datetime, timezone
import unittest.mock as mock
import xml.etree.ElementTree as ET
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import (
    _bind_profile_id,
    _profile_needs_scroll_to_top,
    _try_open_own_root_without_model,
    IdentityProofResult,
    PROFILE_SCROLL_TO_TOP_SWIPES,
)
from phone_runner.mobilerun import MobilerunResult
from phone_runner.publish import _short_error, execute_publish

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100012345678901"
TEST_ROOT_NAME = "Agung"
TEST_DEST_ID = "100012345678901"
TEST_DEST_NAME = "Agung"

FAKE_PROBE_READY = {
    "status": "ready",
    "adb_connected": True,
    "portal_ready": True,
    "screen": "awake",
    "resolution": "1080x2408",
    "rotation": 0,
    "facebook_version": "580.0.0.51.74",
    "portal_version": "0.7.25",
    "checked_at": "2026-10-16T12:00:00Z",
}


@pytest.fixture(autouse=True)
def _common_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com,malicious.evil.com")
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
    monkeypatch.setattr(
        "phone_runner.publish.set_device_clipboard_text",
        lambda s, t: AdbResult(0, "OK", ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.save_composer_observation",
        lambda s, r: "/obs/composer.json",
    )

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

SCROLLED_OWN_FEED_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" bounds="[100,200][300,250]" text="All, 1 of 4" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[100,400][500,450]" text="Agung" package="com.facebook.katana" />
    <node class="android.widget.TextView" bounds="[100,600][500,700]" text="Some post content" package="com.facebook.katana" />
  </node>
</hierarchy>"""

SCROLLED_VISITOR_FEED_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" bounds="[100,200][300,250]" text="All, 1 of 4" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[100,400][300,500]" text="Follow" content-desc="Follow" package="com.facebook.katana" />
  </node>
</hierarchy>"""

VISITOR_PROFILE_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.Button" clickable="true" bounds="[396,1084][606,1166]" text="Agung" content-desc="" package="com.facebook.katana" />
    <node class="android.widget.Button" clickable="true" bounds="[100,300][300,400]" text="Follow" content-desc="Follow" package="com.facebook.katana" />
  </node>
</hierarchy>"""

UNKNOWN_SCREEN_XML = """\
<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>
<hierarchy rotation="0">
  <node index="0" text="" class="android.widget.FrameLayout" package="com.facebook.katana" bounds="[0,0][1080,2408]">
    <node class="android.widget.TextView" bounds="[100,200][300,250]" text="Random Screen" package="com.facebook.katana" />
  </node>
</hierarchy>"""

EMPTY_PROOF_COMPOSER_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="false" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="What's on your mind?" />
</hierarchy>"""

SHARE_INTENT_COMPOSER_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="What's on your mind?" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""

TYPED_SHARE_COMPOSER_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="Fallback test caption" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""

POST_SETTINGS_SCREEN_XML = """<hierarchy rotation="0">
  <node text="Post settings" bounds="[200,100][600,200]" />
  <node class="android.widget.Button" content-desc="SHARE" bounds="[811,125][1026,233]" />
  <node class="android.widget.Button" content-desc="Post audience, Public" bounds="[50,300][1030,450]" />
</hierarchy>"""

MODERN_FEED_DUMP = """\
<hierarchy rotation="0">
  <node resource-id="com.facebook.katana:id/feed_view" text="Feed" />
</hierarchy>"""


# ===========================================================================
# Fix 1: Keep real media staging error text
# ===========================================================================

def test_short_error_collapses_whitespace_and_newlines():
    exc = ValueError("Prohibited IP address '127.0.0.1'\nresolved")
    res = _short_error(exc)
    assert res == "Prohibited IP address '127.0.0.1' resolved"
    assert "\n" not in res


def test_short_error_truncates_at_limit():
    long_msg = "error detail " * 30
    exc = RuntimeError(long_msg)
    res = _short_error(exc, limit=200)
    assert len(res) == 200
    assert res == (" ".join(long_msg.split()))[:200]

    res_short = _short_error(exc, limit=50)
    assert len(res_short) == 50


LEGACY_SESSION_DUMP = f"Intent: act=android.intent.action.VIEW c_user={TEST_ROOT_ID} cmp=com.facebook.katana/.FeedActivity"
LEGACY_TEXT_COMPOSER_DUMP = f"""
<hierarchy rotation="0">
  <node resource-id="com.facebook.katana:id/composer_actor" content-desc="fb://profile/{TEST_DEST_ID}" text="{TEST_DEST_NAME}" />
  <node resource-id="com.facebook.katana:id/composer_text_input" text="Test caption text" />
  <node resource-id="com.facebook.katana:id/composer_publish_button" text="Post" bounds="[900,100][1050,200]" />
</hierarchy>
"""


def test_media_staging_detail_contains_real_error_message(monkeypatch):
    monkeypatch.setattr(
        "phone_runner.publish.probe_device",
        lambda s, wake=False: FAKE_PROBE_READY,
    )
    monkeypatch.setattr(
        "phone_runner.publish.query_ui_hierarchy",
        lambda s: AdbResult(0, LEGACY_TEXT_COMPOSER_DUMP, ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.query_activity_state",
        lambda s: AdbResult(0, LEGACY_SESSION_DUMP, ""),
    )
    monkeypatch.setattr(
        "phone_runner.publish.stage_media_files",
        mock.Mock(side_effect=ValueError("Prohibited IP address '127.0.0.1'\nresolved")),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Caption",
        media_urls=["https://malicious.evil.com/img.png"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "media_staging_failed"
    stage = next((s for s in res["stages"] if s["stage"] == "media_staging"), None)
    assert stage is not None
    assert stage["passed"] is False
    assert stage["detail"] == "Media staging failed: ValueError: Prohibited IP address '127.0.0.1' resolved"


# ===========================================================================
# Fix 2: Scroll profile feed to top when header is above screen
# ===========================================================================

def test_profile_needs_scroll_to_top_truth_table():
    # 1. First profile tab label: "All, 1 of 4" in text -> True
    tree_tab_text = ET.fromstring("<hierarchy><node text='All, 1 of 4' /></hierarchy>")
    assert _profile_needs_scroll_to_top(tree_tab_text) is True

    # 2. First profile tab label: "Posts, 1 of 3" in content-desc -> True
    tree_tab_desc = ET.fromstring("<hierarchy><node content-desc='Posts, 1 of 3' /></hierarchy>")
    assert _profile_needs_scroll_to_top(tree_tab_desc) is True

    # 3. Scrolled marker: "Manage posts" -> True
    tree_manage = ET.fromstring("<hierarchy><node text='Manage posts' /></hierarchy>")
    assert _profile_needs_scroll_to_top(tree_manage) is True

    # 4. Header-only own profile tree (Edit profile, Dashboard, Agung) -> False
    tree_header = ET.fromstring(OWN_PROFILE_XML)
    assert _profile_needs_scroll_to_top(tree_header) is False

    # 5. Non-matching label: "Allison, 1 of 4" -> False
    tree_non_match = ET.fromstring("<hierarchy><node text='Allison, 1 of 4' /></hierarchy>")
    assert _profile_needs_scroll_to_top(tree_non_match) is False

    # 6. Tab 2 of 4 (not first tab) -> False
    tree_tab_second = ET.fromstring("<hierarchy><node text='All, 2 of 4' /></hierarchy>")
    assert _profile_needs_scroll_to_top(tree_tab_second) is False


def test_bind_profile_id_scrolled_feed_swipes_once_to_owner_view(monkeypatch):
    adb_commands = []

    def fake_run_adb(args, serial=None):
        adb_commands.append(list(args))
        return AdbResult(0, "OK", "")

    # First dump = scrolled feed; second dump (after swipe) = owner view
    dumps = [
        (True, "", ET.fromstring(SCROLLED_OWN_FEED_XML), SCROLLED_OWN_FEED_XML),
        (True, "", ET.fromstring(OWN_PROFILE_XML), OWN_PROFILE_XML),
    ]
    dump_iter = iter(dumps)

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake_run_adb)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_profile_by_id", lambda s, pid: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(dump_iter))

    ok, err, tree, raw_xml = _bind_profile_id(TEST_SERIAL, TEST_ROOT_ID, "Agung")

    assert ok is True
    assert err == ""
    assert tree is not None
    # Exactly one swipe command was sent to scroll back to top
    swipe_cmds = [c for c in adb_commands if len(c) >= 3 and c[1:3] == ["input", "swipe"]]
    assert len(swipe_cmds) == 1
    assert swipe_cmds[0] == ["shell", "input", "swipe", "540", "700", "540", "1900", "300"]
    # No taps sent
    tap_cmds = [c for c in adb_commands if len(c) >= 3 and c[1:3] == ["input", "tap"]]
    assert len(tap_cmds) == 0


def test_bind_profile_id_scrolled_visitor_profile_fails_closed(monkeypatch):
    adb_commands = []

    def fake_run_adb(args, serial=None):
        adb_commands.append(list(args))
        return AdbResult(0, "OK", "")

    # First dump = scrolled visitor feed; all subsequent dumps = visitor profile
    dumps = [
        (True, "", ET.fromstring(SCROLLED_VISITOR_FEED_XML), SCROLLED_VISITOR_FEED_XML),
        (True, "", ET.fromstring(VISITOR_PROFILE_XML), VISITOR_PROFILE_XML),
        (True, "", ET.fromstring(VISITOR_PROFILE_XML), VISITOR_PROFILE_XML),
        (True, "", ET.fromstring(VISITOR_PROFILE_XML), VISITOR_PROFILE_XML),
        (True, "", ET.fromstring(VISITOR_PROFILE_XML), VISITOR_PROFILE_XML),
        (True, "", ET.fromstring(VISITOR_PROFILE_XML), VISITOR_PROFILE_XML),
    ]
    dump_iter = iter(dumps)

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake_run_adb)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_profile_by_id", lambda s, pid: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(dump_iter))

    ok, err, tree, raw_xml = _bind_profile_id(TEST_SERIAL, TEST_ROOT_ID, "Agung")

    assert ok is False
    assert "Profile id binding failed" in err
    assert "visitor profile marker" in err or "owner control" in err


def test_try_open_own_root_without_model_swipes_on_scrolled_tabs_profile(monkeypatch):
    adb_commands = []

    def fake_run_adb(args, serial=None):
        adb_commands.append(list(args))
        return AdbResult(0, "OK", "")

    # Step 1: initial screen (unknown)
    # Step 2: after fb://feed (unknown)
    # Step 3: after fb://profile (scrolled profile with tabs, no owner header)
    # Step 4: after swipe (owner root view)
    dumps = [
        (True, "", ET.fromstring(UNKNOWN_SCREEN_XML), UNKNOWN_SCREEN_XML),
        (True, "", ET.fromstring(UNKNOWN_SCREEN_XML), UNKNOWN_SCREEN_XML),
        (True, "", ET.fromstring(SCROLLED_OWN_FEED_XML), SCROLLED_OWN_FEED_XML),
        (True, "", ET.fromstring(OWN_PROFILE_XML), OWN_PROFILE_XML),
    ]
    dump_iter = iter(dumps)

    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake_run_adb)
    monkeypatch.setattr("phone_runner.identity_proof.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_feed", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.identity_proof.launch_facebook_own_profile", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.identity_proof.verify_foreground_facebook", lambda s, geom=None: next(dump_iter))

    res = _try_open_own_root_without_model(TEST_SERIAL, "Agung", allow_name_identity=True)

    assert res is True
    swipe_cmds = [c for c in adb_commands if len(c) >= 3 and c[1:3] == ["input", "swipe"]]
    assert len(swipe_cmds) >= 1
    assert swipe_cmds[0] == ["shell", "input", "swipe", "540", "700", "540", "1900", "300"]


# ===========================================================================
# Fix 3: Caption typing fallback runs with Portal keyboard selected
# ===========================================================================

def _make_dummy_proof() -> IdentityProofResult:
    return IdentityProofResult(
        nonce="nonce_test",
        created_at=datetime.now(timezone.utc).isoformat(),
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
            "empty_state_certainty": True,
            "header_variant": "actor_title",
        },
    )


def test_caption_fallback_runs_inside_portal_ime_context(monkeypatch):
    class FakePortalImeContext:
        active = False
        entered = False
        exited = False

        def __init__(self, serial: str):
            self.serial = serial

        def __enter__(self):
            FakePortalImeContext.active = True
            FakePortalImeContext.entered = True
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            FakePortalImeContext.active = False
            FakePortalImeContext.exited = True
            return False

    called_inside_portal_ime = []

    def fake_type_text(serial: str, text: str, clear: bool = False, **kw):
        called_inside_portal_ime.append(FakePortalImeContext.active)
        return MobilerunResult(0, "OK", "")

    proof = _make_dummy_proof()
    taps = []

    monkeypatch.setattr("phone_runner.publish.PortalImeContext", FakePortalImeContext)
    monkeypatch.setattr("phone_runner.publish.type_text", fake_type_text)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    # UI hierarchy sequence:
    # 1: initial feed dump
    # 2: empty composer dump
    # 3: composer dump after paste (STILL empty -> triggers fallback)
    # 4: composer dump after fallback typing (has matching caption)
    # 5: post settings screen (SHARE button)
    ui_dumps = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_123", "https://facebook.com/posts/post_123", "resolved"),
    )

    caption = "Fallback test caption"
    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["evidence"]["caption_entry"] == "paste_ignored_then_type"
    assert FakePortalImeContext.entered is True
    assert FakePortalImeContext.exited is True
    assert called_inside_portal_ime == [True]


def test_caption_fallback_portal_ime_exception_fails_closed(monkeypatch):
    class BrokenPortalImeContext:
        def __init__(self, serial: str):
            self.serial = serial

        def __enter__(self):
            raise RuntimeError("Failed to query default IME")

        def __exit__(self, exc_type, exc_val, exc_tb):
            return False

    proof = _make_dummy_proof()

    monkeypatch.setattr("phone_runner.publish.PortalImeContext", BrokenPortalImeContext)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_dumps = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
    ]
    ui_iter = iter(ui_dumps)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )

    caption = "Fallback test caption"
    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "caption_mismatch"
    assert res["evidence"]["caption_entry"] == "paste_ignored_then_type"
