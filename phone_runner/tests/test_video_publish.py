from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import IdentityProofResult
from phone_runner.mobilerun import MobilerunResult
from phone_runner.publish import execute_publish

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100012345678901"
TEST_ROOT_NAME = "OwnRootUser"
TEST_DEST_ID = "100098765432109"
TEST_DEST_NAME = "OwnPageTarget"

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

MODERN_FEED_DUMP = """
<hierarchy rotation="0">
  <node resource-id="com.facebook.katana:id/feed_view" text="Feed" />
</hierarchy>
"""

EMPTY_PROOF_COMPOSER_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="false" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="What's on your mind?" />
  <node class="android.widget.Button" bounds="[72,1379][204,1511]" content-desc="Gallery" />
</hierarchy>"""

VIDEO_SHARE_COMPOSER_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="What's on your mind?" />
  <node class="android.widget.Button" content-desc="Video" bounds="[0,891][1080,2104]" />
  <node class="android.widget.Button" content-desc="Edit video" bounds="[600,891][800,1000]" />
  <node class="android.widget.Button" content-desc="Remove video" bounds="[800,891][1000,1000]" />
</hierarchy>"""

PHOTO_SHARE_COMPOSER_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="What's on your mind?" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""

POST_SETTINGS_SCREEN_XML = """<hierarchy rotation="0">
  <node text="Post settings" bounds="[200,100][600,200]" />
  <node class="android.widget.Button" content-desc="SHARE" bounds="[811,125][1026,233]" />
  <node class="android.widget.Button" content-desc="Post audience, Public" bounds="[50,300][1030,450]" />
</hierarchy>"""


def _make_typed_video_composer_xml(caption: str) -> str:
    return f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="{caption}" />
  <node class="android.widget.Button" content-desc="Video" bounds="[0,891][1080,2104]" />
  <node class="android.widget.Button" content-desc="Edit video" bounds="[600,891][800,1000]" />
  <node class="android.widget.Button" content-desc="Remove video" bounds="[800,891][1000,1000]" />
</hierarchy>"""


def _make_typed_photo_composer_xml(caption: str) -> str:
    return f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="{caption}" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""


def _make_reel_settings_xml(caption: str) -> str:
    return f"""<hierarchy rotation="0">
  <node class="android.view.ViewGroup" text="Reel Settings" bounds="[0,0][1080,216]" />
  <node class="android.widget.Button" content-desc="Back" bounds="[30,108][138,216]" />
  <node class="android.widget.AutoCompleteTextView" bounds="[330,276][1080,696]" text="{caption}" content-desc="Describe your reel. You can also add hashtags here." />
  <node class="android.widget.Button" content-desc="Edit cover" bounds="[30,276][300,696]" />
  <node class="android.widget.Button" clickable="true" content-desc="Share now" bounds="[48,2096][1032,2228]" />
</hierarchy>"""


def _make_valid_modern_proof() -> IdentityProofResult:
    return IdentityProofResult(
        nonce="nonce_video_123",
        created_at="2026-10-17T12:00:00Z",
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
        stages=[
            {"stage": "root_url", "passed": True, "detail": "Verified root"},
            {"stage": "composer_binding", "passed": True, "detail": "Bound composer"},
        ],
        composer_readiness={
            "has_header": True,
            "has_close": True,
            "input_class": "android.widget.AutoCompleteTextView",
            "author_matched": True,
            "empty_state_certainty": True,
            "header_variant": "actor_title",
        },
    )


@pytest.fixture(autouse=True)
def _common_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "52428800")
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


def test_video_happy_path(monkeypatch):
    """(a) video happy path: mime video/mp4, 4 gates (composer_before, composer_after, reel_settings, posted),
    Share now tapped exactly once, resolve called with attempts=8, wait=15.0, status published."""
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    caption = "Watch this great reel"
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, _make_typed_video_composer_xml(caption), ""),
        AdbResult(0, _make_reel_settings_xml(caption), ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Movies/staging/stage_1.mp4", "local_path": "/tmp/test.mp4"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/video/media/201")

    share_intent_calls = []

    def mock_open_share_intent(serial, uri, author, mime_type="image/jpeg"):
        share_intent_calls.append({"serial": serial, "uri": uri, "author": author, "mime_type": mime_type})
        return True, "opened", VIDEO_SHARE_COMPOSER_XML

    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", mock_open_share_intent)
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    gates_called = []

    def mock_verify_gate(serial, gate, **kw):
        gates_called.append(gate)
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    resolve_calls = []

    def mock_resolve(serial, caption, attempts=4, wait=10.0):
        resolve_calls.append({"attempts": attempts, "wait": wait})
        return "reel_post_123", "https://facebook.com/reel/123", "resolved reel"

    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", mock_resolve)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/v.mp4"],
        media_kind="video",
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "reel_post_123"
    assert res["evidence"]["media_type"] == "video"
    assert len(share_intent_calls) == 1
    assert share_intent_calls[0]["mime_type"] == "video/mp4"
    assert gates_called == ["composer_before_typing", "composer_after_typing", "reel_settings", "posted"]

    share_now_bounds = (48, 2096, 1032, 2228)
    share_now_taps = [b for b in taps if b == share_now_bounds]
    assert len(share_now_taps) == 1

    assert len(resolve_calls) == 1
    assert resolve_calls[0] == {"attempts": 8, "wait": 15.0}


def test_video_rehearsal_stops_before_share_now(monkeypatch):
    """(b) video rehearsal=True -> 'rehearsal_ok', 'Share now' never tapped."""
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    caption = "Rehearsal caption"
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, _make_typed_video_composer_xml(caption), ""),
        AdbResult(0, _make_reel_settings_xml(caption), ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Movies/staging/stage_1.mp4", "local_path": "/tmp/test.mp4"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/video/media/201")
    monkeypatch.setattr(
        "phone_runner.publish.open_share_intent_composer",
        lambda s, u, a, mime_type="image/jpeg": (True, "opened", VIDEO_SHARE_COMPOSER_XML),
    )
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/v.mp4"],
        media_kind="video",
        rehearsal=True,
    )

    assert res["status"] == "rehearsal_ok"
    share_now_bounds = (48, 2096, 1032, 2228)
    assert share_now_bounds not in taps


def test_reel_settings_caption_mismatch_fails_and_discards(monkeypatch):
    """(c) Reel Settings caption differs from expected -> caption_mismatch, no Share now tap."""
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    caption = "Expected caption"
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, _make_typed_video_composer_xml(caption), ""),
        AdbResult(0, _make_reel_settings_xml("Corrupted different caption"), ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Movies/staging/stage_1.mp4", "local_path": "/tmp/test.mp4"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/video/media/201")
    monkeypatch.setattr(
        "phone_runner.publish.open_share_intent_composer",
        lambda s, u, a, mime_type="image/jpeg": (True, "opened", VIDEO_SHARE_COMPOSER_XML),
    )
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}),
    )

    discard_called = []
    monkeypatch.setattr("phone_runner.publish.discard_composer", lambda s: discard_called.append(s))

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/v.mp4"],
        media_kind="video",
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "caption_mismatch"
    share_now_bounds = (48, 2096, 1032, 2228)
    assert share_now_bounds not in taps
    assert len(discard_called) >= 1


def test_reel_settings_unrecognized_screen_fails(monkeypatch):
    """(d) Reel Settings not recognised (Post settings XML instead) -> share_screen_unrecognized."""
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    caption = "Video caption"
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, _make_typed_video_composer_xml(caption), ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),  # Wrong screen for video!
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Movies/staging/stage_1.mp4", "local_path": "/tmp/test.mp4"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/video/media/201")
    monkeypatch.setattr(
        "phone_runner.publish.open_share_intent_composer",
        lambda s, u, a, mime_type="image/jpeg": (True, "opened", VIDEO_SHARE_COMPOSER_XML),
    )
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/v.mp4"],
        media_kind="video",
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "share_screen_unrecognized"


def test_video_never_sends_reference_bytes_to_verify_gate(monkeypatch):
    """(e) video never sends reference bytes to verify_gate (assert reference_jpeg is None for every gate call)."""
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    caption = "Video reference test"
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, _make_typed_video_composer_xml(caption), ""),
        AdbResult(0, _make_reel_settings_xml(caption), ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Movies/staging/stage_1.mp4", "local_path": "/tmp/test.mp4"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/video/media/201")
    monkeypatch.setattr(
        "phone_runner.publish.open_share_intent_composer",
        lambda s, u, a, mime_type="image/jpeg": (True, "opened", VIDEO_SHARE_COMPOSER_XML),
    )
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    ref_jpegs = []

    def mock_verify_gate(serial, gate, **kw):
        ref_jpegs.append(kw.get("reference_jpeg"))
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("reel_post_456", "https://facebook.com/reel/456", "resolved"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/v.mp4"],
        media_kind="video",
    )

    assert res["status"] == "published"
    assert len(ref_jpegs) == 4
    for ref in ref_jpegs:
        assert ref is None


def test_photo_happy_path_still_unchanged(monkeypatch):
    """(f) photo happy path still: gate 'post_settings', mime 'image/jpeg', resolve with default attempts."""
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    caption = "Photo caption"
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, _make_typed_photo_composer_xml(caption), ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")

    share_intent_calls = []

    def mock_open_share_intent(serial, uri, author, mime_type="image/jpeg"):
        share_intent_calls.append({"mime_type": mime_type})
        return True, "opened", PHOTO_SHARE_COMPOSER_XML

    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", mock_open_share_intent)
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    gates_called = []

    def mock_verify_gate(serial, gate, **kw):
        gates_called.append(gate)
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    resolve_calls = []

    def mock_resolve(serial, caption, attempts=4, wait=10.0):
        resolve_calls.append({"attempts": attempts, "wait": wait})
        return "photo_post_123", "https://facebook.com/posts/photo_123", "resolved photo"

    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", mock_resolve)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["evidence"]["media_type"] == "photo"
    assert len(share_intent_calls) == 1
    assert share_intent_calls[0]["mime_type"] == "image/jpeg"
    assert gates_called == ["composer_before_typing", "composer_after_typing", "post_settings", "posted"]
    assert (811, 125, 1026, 233) in taps  # Photo SHARE button tapped
    assert len(resolve_calls) == 1
    assert resolve_calls[0] == {"attempts": 4, "wait": 10.0}
