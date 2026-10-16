import json
import threading
from unittest import mock
import urllib.error
import urllib.request
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import IdentityProofResult
from phone_runner.mobilerun import MobilerunResult
from phone_runner.publish import (
    PRE_SHARE_MAX_SECONDS,
    PUBLISH_BUDGET_SECONDS,
    execute_publish,
)
from phone_runner.server import create_server

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100012345678901"
TEST_ROOT_NAME = "OwnRootUser"
TEST_DEST_ID = "100098765432109"
TEST_DEST_NAME = "OwnPageTarget"
TEST_TOKEN = "secret-test-token-12345"

SHARE_BOUNDS = (811, 125, 1026, 233)
TEST_CAPTION = "Hello with modern share intent"

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
  <node class="android.widget.Button" bounds="[30,0][138,100]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,0][1068,100]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="{TEST_CAPTION}" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""

POST_SETTINGS_SCREEN_XML = """<hierarchy rotation="0">
  <node text="Post settings" bounds="[200,100][600,200]" />
  <node class="android.widget.Button" content-desc="SHARE" bounds="[811,125][1026,233]" />
  <node class="android.widget.Button" content-desc="Post audience, Public" bounds="[50,300][1030,450]" />
</hierarchy>"""

LEGACY_SESSION_DUMP = f"Intent: act=android.intent.action.VIEW c_user={TEST_ROOT_ID} cmp=com.facebook.katana/.FeedActivity"
LEGACY_COMPOSER_DUMP = f"""
<hierarchy rotation="0">
  <node resource-id="com.facebook.katana:id/composer_actor" content-desc="fb://profile/{TEST_DEST_ID}" text="{TEST_DEST_NAME}" />
  <node resource-id="com.facebook.katana:id/composer_text_input" text="Test caption" />
  <node resource-id="com.facebook.katana:id/composer_publish_button" text="Post" bounds="[900,100][1050,200]" />
</hierarchy>
"""


@pytest.fixture(autouse=True)
def _common_env(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "5242880")
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", TEST_TOKEN)
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


def _make_valid_modern_proof() -> IdentityProofResult:
    return IdentityProofResult(
        nonce="nonce_123",
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


def test_rehearsal_true_happy_path(monkeypatch):
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    def spy_human_tap(s, bounds, **k):
        taps.append(bounds)

    monkeypatch.setattr("phone_runner.publish.human_tap", spy_human_tap)

    adb_calls = []
    def spy_run_adb(args, serial=None):
        adb_calls.append(args)
        if len(args) >= 4 and args[0] == "shell" and args[1] == "input" and args[2] == "tap":
            try:
                taps.append((int(args[3]), int(args[4]), int(args[3]), int(args[4])))
            except ValueError:
                pass
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.publish.run_adb", spy_run_adb)

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False: MobilerunResult(0, "OK", ""))

    mock_discard = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.discard_composer", mock_discard)

    mock_resolve = mock.Mock(return_value=("post_123", "https://fb.com/123", "resolved"))
    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", mock_resolve)

    def mock_verify_gate(serial, gate, **kw):
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=TEST_CAPTION,
        media_urls=["https://cdn.example.com/p.jpg"],
        rehearsal=True,
    )

    assert res["status"] == "rehearsal_ok"
    assert res["facebook_post_id"] is None
    assert res["permalink"] is None
    assert res["actor_id"] == TEST_DEST_ID
    assert res["publish_may_have_started"] is False
    assert res["error_code"] is None
    assert res["evidence"]["pre_share_seconds"] >= 0

    rehearsal_stage = next((s for s in res["stages"] if s["stage"] == "rehearsal_stop"), None)
    assert rehearsal_stage is not None
    assert rehearsal_stage["passed"] is True
    assert "stopped before SHARE" in rehearsal_stage["detail"]

    # discard_composer called (once in step b for proof composer, once at rehearsal stop)
    assert mock_discard.call_count == 2
    mock_discard.assert_called_with(TEST_SERIAL)

    # resolve_latest_post_link NOT called
    mock_resolve.assert_not_called()

    # No tap inside SHARE bounds
    sx1, sy1, sx2, sy2 = SHARE_BOUNDS
    for t in taps:
        if isinstance(t, tuple) and len(t) == 4:
            tx1, ty1, tx2, ty2 = t
            tap_x = (tx1 + tx2) // 2
            tap_y = (ty1 + ty2) // 2
            assert not (sx1 <= tap_x <= sx2 and sy1 <= tap_y <= sy2), f"Tap at ({tap_x}, {tap_y}) was inside SHARE bounds"


def test_rehearsal_true_text_only_happy_path(monkeypatch):
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    text_composer_typed_xml = f"""<hierarchy rotation="0">
      <node class="android.widget.Button" bounds="[30,0][138,100]" content-desc="Close" />
      <node class="android.widget.Button" bounds="[810,0][1068,100]" content-desc="Next" enabled="true" />
      <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
      <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="{TEST_CAPTION}" />
    </hierarchy>"""

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, text_composer_typed_xml, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False: MobilerunResult(0, "OK", ""))

    mock_discard = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.discard_composer", mock_discard)

    mock_resolve = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", mock_resolve)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=TEST_CAPTION,
        media_urls=None,
        rehearsal=True,
    )

    assert res["status"] == "rehearsal_ok"
    assert res["publish_may_have_started"] is False
    assert mock_discard.call_count == 1
    mock_resolve.assert_not_called()
    sx1, sy1, sx2, sy2 = SHARE_BOUNDS
    for t in taps:
        if isinstance(t, tuple) and len(t) == 4:
            tx1, ty1, tx2, ty2 = t
            tap_x = (tx1 + tx2) // 2
            tap_y = (ty1 + ty2) // 2
            assert not (sx1 <= tap_x <= sx2 and sy1 <= tap_y <= sy2)


def test_rehearsal_false_taps_share(monkeypatch):
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False: MobilerunResult(0, "OK", ""))

    mock_discard = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.discard_composer", mock_discard)

    mock_resolve = mock.Mock(return_value=("post_123", "https://fb.com/123", "resolved"))
    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", mock_resolve)

    def mock_verify_gate(serial, gate, **kw):
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=TEST_CAPTION,
        media_urls=["https://cdn.example.com/p.jpg"],
        rehearsal=False,
    )

    assert res["status"] == "published"
    assert res["publish_may_have_started"] is True
    share_taps = [b for b in taps if b == SHARE_BOUNDS]
    assert len(share_taps) == 1
    mock_resolve.assert_called_once()
    # discard_composer was only called in step b to close proof composer, NOT at submit
    assert mock_discard.call_count == 1


def test_rehearsal_legacy_unsupported():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, LEGACY_COMPOSER_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish.type_text") as mock_type,
        mock.patch("phone_runner.publish.human_tap") as mock_tap,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test legacy",
            rehearsal=True,
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "rehearsal_unsupported_layout"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    mock_tap.assert_not_called()


def test_time_budget_pre_share_exceeded(monkeypatch):
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    clock = [100.0]
    def fake_monotonic():
        return clock[0]

    monkeypatch.setattr("phone_runner.publish.time.monotonic", fake_monotonic)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False: MobilerunResult(0, "OK", ""))

    mock_discard = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.discard_composer", mock_discard)

    def mock_verify_gate(serial, gate, **kw):
        if gate == "post_settings":
            # Simulate time passing past PRE_SHARE_MAX_SECONDS
            clock[0] += PRE_SHARE_MAX_SECONDS + 10.0
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=TEST_CAPTION,
        media_urls=["https://cdn.example.com/p.jpg"],
        rehearsal=False,
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "time_budget_exceeded"
    assert res["publish_may_have_started"] is False
    assert res["evidence"]["pre_share_seconds"] > PRE_SHARE_MAX_SECONDS

    tb_stage = next((s for s in res["stages"] if s["stage"] == "time_budget"), None)
    assert tb_stage is not None
    assert tb_stage["passed"] is False
    assert "pre-SHARE time budget exceeded" in tb_stage["detail"]

    # discard_composer called in step b, and called when time budget exceeded
    assert mock_discard.call_count == 2
    share_taps = [b for b in taps if b == SHARE_BOUNDS]
    assert len(share_taps) == 0


def test_time_budget_post_share_skips_posted_gate(monkeypatch):
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    clock = [100.0]
    def fake_monotonic():
        return clock[0]

    monkeypatch.setattr("phone_runner.publish.time.monotonic", fake_monotonic)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False: MobilerunResult(0, "OK", ""))

    def fake_resolve(serial, caption):
        # Advance time to exceed PUBLISH_BUDGET_SECONDS - 20 (e.g. +235s)
        clock[0] += (PUBLISH_BUDGET_SECONDS - 20) + 5.0
        return "post_budget_123", "https://fb.com/post_budget_123", "resolved"

    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", fake_resolve)

    gates_called = []
    def mock_verify_gate(serial, gate, **kw):
        gates_called.append(gate)
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=TEST_CAPTION,
        media_urls=["https://cdn.example.com/p.jpg"],
        rehearsal=False,
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_budget_123"
    assert res["publish_may_have_started"] is True
    assert res["evidence"].get("posted_gate_skipped") == "time_budget"
    assert "posted" not in gates_called


def test_time_budget_post_share_unresolved_uncertain(monkeypatch):
    proof = _make_valid_modern_proof()
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    clock = [100.0]
    def fake_monotonic():
        return clock[0]

    monkeypatch.setattr("phone_runner.publish.time.monotonic", fake_monotonic)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: None)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False: MobilerunResult(0, "OK", ""))

    def fake_resolve_unresolved(serial, caption):
        clock[0] += (PUBLISH_BUDGET_SECONDS - 20) + 5.0
        return None, None, "feed post link unresolved"

    monkeypatch.setattr("phone_runner.publish.resolve_latest_post_link", fake_resolve_unresolved)
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda s, g, **kw: (True, {"gate": g, "ok": True, "detail": "ok", "answer": {}}),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=TEST_CAPTION,
        media_urls=["https://cdn.example.com/p.jpg"],
        rehearsal=False,
    )

    assert res["status"] == "uncertain"
    assert res["error_code"] == "post_identity_unproven"
    assert res["publish_may_have_started"] is True


@pytest.fixture
def running_server(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_TOKEN", TEST_TOKEN)
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_BIND", "127.0.0.1")
    monkeypatch.setenv("PHONE_RUNNER_PORT", "0")
    server = create_server("127.0.0.1", 0)
    host, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()


def _make_server_request(
    url: str,
    method: str = "POST",
    headers: dict | None = None,
    data: dict | None = None,
):
    h = headers.copy() if headers else {}
    body = json.dumps(data).encode("utf-8") if data is not None else None
    if data is not None:
        h.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8")) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        parsed = {}
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except Exception:
            pass
        return exc.code, parsed


def test_server_rehearse_and_publish_routes(running_server):
    with mock.patch("phone_runner.server.execute_publish") as mock_pub:
        mock_pub.return_value = {
            "status": "rehearsal_ok",
            "facebook_post_id": None,
            "permalink": None,
            "publish_may_have_started": False,
        }

        # 1. POST /rehearse calls execute_publish with rehearsal=True
        status, body = _make_server_request(
            f"{running_server}/rehearse",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={
                "serial": TEST_SERIAL,
                "main_profile": {"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
                "destination": {"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
                "caption": "Rehearse test",
            },
        )
        assert status == 200
        assert body["status"] == "rehearsal_ok"
        mock_pub.assert_called_once()
        assert mock_pub.call_args.kwargs.get("rehearsal") is True

    with mock.patch("phone_runner.server.execute_publish") as mock_pub:
        mock_pub.return_value = {
            "status": "published",
            "facebook_post_id": "123",
            "permalink": "https://fb.com/123",
            "publish_may_have_started": True,
        }

        # 2. POST /publish calls execute_publish with rehearsal False (not passed or False)
        status, body = _make_server_request(
            f"{running_server}/publish",
            method="POST",
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
            data={
                "serial": TEST_SERIAL,
                "main_profile": {"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
                "destination": {"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
                "caption": "Publish test",
            },
        )
        assert status == 200
        assert body["status"] == "published"
        mock_pub.assert_called_once()
        assert not mock_pub.call_args.kwargs.get("rehearsal", False)
