from datetime import datetime
import json
import os
from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import IdentityProofResult
from phone_runner.mobilerun import MobilerunResult
from phone_runner.observation import save_composer_observation
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

LEGACY_SESSION_DUMP = f"Intent: act=android.intent.action.VIEW c_user={TEST_ROOT_ID} cmp=com.facebook.katana/.FeedActivity"
LEGACY_COMPOSER_DUMP = f"""
<hierarchy rotation="0">
  <node resource-id="com.facebook.katana:id/composer_actor" content-desc="fb://profile/{TEST_DEST_ID}" text="{TEST_DEST_NAME}" />
  <node resource-id="com.facebook.katana:id/composer_text_input" text="Test caption" />
  <node resource-id="com.facebook.katana:id/composer_publish_button" text="Post" bounds="[900,100][1050,200]" />
</hierarchy>
"""

MODERN_FEED_DUMP = """
<hierarchy rotation="0">
  <node resource-id="com.facebook.katana:id/feed_view" text="Feed" />
</hierarchy>
"""


def _make_valid_modern_proof(
    empty_state_certainty: bool | None = False,
    header_variant: str = "actor_title",
) -> IdentityProofResult:
    return IdentityProofResult(
        nonce="nonce_r6_test",
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
            "header_variant": header_variant,
        },
    )


def test_save_composer_observation_writes_json_keys_and_permissions(tmp_path, monkeypatch):
    obs_dir = tmp_path / "custom_observations"
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(obs_dir))

    fake_xml = "<hierarchy><node text='ModernComposer'/></hierarchy>"

    def fake_query_ui(serial):
        assert serial == TEST_SERIAL
        return AdbResult(0, fake_xml, "")

    def fake_run_adb(args, serial=None, timeout=None):
        cmd = " ".join(args)
        if "com.mobilerun.portal/state" in cmd:
            return AdbResult(0, "mock_portal_state_data", "")
        if "dumpsys window" in cmd:
            return AdbResult(0, "mCurrentFocus=Window{com.facebook.katana/ComposerActivity}", "")
        if "dumpsys input_method" in cmd:
            return AdbResult(0, "mCurSelectedId=com.mobilerun.portal/.PortalIME\nmInputShown=true", "")
        return AdbResult(0, "", "")

    monkeypatch.setattr("phone_runner.observation.query_ui_hierarchy", fake_query_ui)
    monkeypatch.setattr("phone_runner.observation.run_adb", fake_run_adb)

    result_path = save_composer_observation(TEST_SERIAL, "draft_state_uncertain")

    assert result_path is not None
    assert os.path.isfile(result_path)

    # Check directory permissions: 0o700
    dir_stat = os.stat(str(obs_dir))
    assert dir_stat.st_mode & 0o777 == 0o700

    # Check file permissions: 0o600
    file_stat = os.stat(result_path)
    assert file_stat.st_mode & 0o777 == 0o600

    # Check JSON content and expected keys
    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    expected_keys = {"reason", "captured_at", "focus", "ime", "ui_xml", "portal_state"}
    assert set(data.keys()) == expected_keys
    assert data["reason"] == "draft_state_uncertain"
    assert data["ui_xml"] == fake_xml
    assert "com.facebook.katana/ComposerActivity" in data["focus"]
    assert "mCurSelectedId=com.mobilerun.portal/.PortalIME" in data["ime"]
    assert data["portal_state"] == "mock_portal_state_data"
    # Ensure captured_at is valid ISO timestamp
    assert datetime.fromisoformat(data["captured_at"]) is not None


def test_portal_state_truncated_at_200k_chars(tmp_path, monkeypatch):
    obs_dir = tmp_path / "obs"
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(obs_dir))

    huge_state = "A" * 300_000

    monkeypatch.setattr(
        "phone_runner.observation.query_ui_hierarchy",
        lambda s: AdbResult(0, "<hierarchy/>", ""),
    )
    monkeypatch.setattr(
        "phone_runner.observation.run_adb",
        lambda args, serial=None, timeout=None: AdbResult(0, huge_state, "") if "portal/state" in " ".join(args) else AdbResult(0, "", ""),
    )

    result_path = save_composer_observation(TEST_SERIAL, "submission_flow_uncalibrated")
    assert result_path is not None

    with open(result_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert len(data["portal_state"]) == 200_000


def test_rotation_keeps_20_newest_files(tmp_path, monkeypatch):
    obs_dir = tmp_path / "obs_rotation"
    obs_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(obs_dir))

    # Pre-populate with 25 files with distinct mtimes
    created_files = []
    for i in range(25):
        fpath = obs_dir / f"20261016T1000{i:02d}Z_old_reason.json"
        fpath.write_text("{}", encoding="utf-8")
        os.utime(str(fpath), (1000 + i, 1000 + i))
        created_files.append(str(fpath))

    monkeypatch.setattr(
        "phone_runner.observation.query_ui_hierarchy",
        lambda s: AdbResult(0, "<hierarchy/>", ""),
    )
    monkeypatch.setattr(
        "phone_runner.observation.run_adb",
        lambda args, serial=None, timeout=None: AdbResult(0, "ok", ""),
    )

    # Calling save_composer_observation writes 1 new file (making 26), then prunes down to 20
    new_path = save_composer_observation(TEST_SERIAL, "media_unsupported_on_modern")
    assert new_path is not None

    remaining_files = sorted(os.listdir(str(obs_dir)))
    assert len(remaining_files) == 20

    # Oldest 6 files (created_files[:6]) must have been deleted
    for old_file in created_files[:6]:
        assert not os.path.exists(old_file)

    # 19 newer existing files + the 1 new file must remain
    for kept_file in created_files[6:]:
        assert os.path.exists(kept_file)
    assert os.path.exists(new_path)


def test_capture_failure_returns_none_no_exception(tmp_path, monkeypatch):
    obs_dir = tmp_path / "obs_fail"
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(obs_dir))

    # Case 1: query_ui_hierarchy returns returncode != 0
    monkeypatch.setattr(
        "phone_runner.observation.query_ui_hierarchy",
        lambda s: AdbResult(-1, "", "uiautomator error"),
    )
    monkeypatch.setattr(
        "phone_runner.observation.run_adb",
        lambda args, serial=None, timeout=None: AdbResult(0, "", ""),
    )
    res1 = save_composer_observation(TEST_SERIAL, "draft_state_uncertain")
    assert res1 is None

    # Case 2: query_ui_hierarchy returns empty stdout
    monkeypatch.setattr(
        "phone_runner.observation.query_ui_hierarchy",
        lambda s: AdbResult(0, "", ""),
    )
    res2 = save_composer_observation(TEST_SERIAL, "draft_state_uncertain")
    assert res2 is None

    # Case 3: query_ui_hierarchy raises an exception
    def _exploding_hierarchy(s):
        raise RuntimeError("Fatal ADB pipe break")

    monkeypatch.setattr("phone_runner.observation.query_ui_hierarchy", _exploding_hierarchy)
    res3 = save_composer_observation(TEST_SERIAL, "draft_state_uncertain")
    assert res3 is None

    # Case 4: File write / directory failure
    monkeypatch.setattr(
        "phone_runner.observation.query_ui_hierarchy",
        lambda s: AdbResult(0, "<hierarchy/>", ""),
    )
    monkeypatch.setattr("os.makedirs", mock.Mock(side_effect=PermissionError("Permission denied")))
    res4 = save_composer_observation(TEST_SERIAL, "draft_state_uncertain")
    assert res4 is None


def test_publish_modern_gates_call_observation_once_and_set_evidence(tmp_path, monkeypatch):
    obs_dir = tmp_path / "obs_publish"
    monkeypatch.setenv("PHONE_RUNNER_OBSERVATION_DIR", str(obs_dir))
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "5242880")

    monkeypatch.setattr("phone_runner.publish.ping_portal", lambda s: AdbResult(0, "pong\n", ""))
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, **kw: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr(
        "phone_runner.publish.query_ui_hierarchy",
        lambda s: AdbResult(0, MODERN_FEED_DUMP, ""),
    )

    # 1. media_attach_failed gate
    proof_media = _make_valid_modern_proof(empty_state_certainty=True)
    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/p.jpg", "url": "https://cdn.example.com/photo.jpg"}]
    empty_comp_xml = f"""<hierarchy rotation="0">
      <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
      <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="false" />
      <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
      <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="What's on your mind?" />
    </hierarchy>"""
    with (
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof_media),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, empty_comp_xml, "")),
        mock.patch("phone_runner.publish.stage_media_files", return_value=mock_stage),
        mock.patch("phone_runner.publish.attach_first_gallery_item", return_value=(False, "attach failed")),
        mock.patch("phone_runner.publish.save_composer_observation", return_value="/obs/media_gate.json") as mock_obs,
    ):
        res_media = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post with photo",
            media_urls=["https://cdn.example.com/photo.jpg"],
        )
    assert res_media["status"] == "failed"
    assert res_media["error_code"] == "media_attach_failed"
    mock_obs.assert_called_once_with(TEST_SERIAL, "media_attach_failed")
    assert res_media["evidence"]["observation_file"] == "/obs/media_gate.json"

    # 2. draft_state_uncertain gate
    proof_draft = _make_valid_modern_proof(empty_state_certainty=False)
    with (
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof_draft),
        mock.patch("phone_runner.publish.save_composer_observation", return_value="/obs/draft_gate.json") as mock_obs,
    ):
        res_draft = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post text",
        )
    assert res_draft["status"] == "failed"
    assert res_draft["error_code"] == "draft_state_uncertain"
    mock_obs.assert_called_once_with(TEST_SERIAL, "draft_state_uncertain")
    assert res_draft["evidence"]["observation_file"] == "/obs/draft_gate.json"

    # 3. share_screen_unrecognized gate
    proof_submit = _make_valid_modern_proof(empty_state_certainty=True)
    typed_comp_xml = f"""<hierarchy rotation="0">
      <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
      <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
      <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
      <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="Modern post text" />
    </hierarchy>"""
    from phone_runner.mobilerun import MobilerunResult
    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, empty_comp_xml, ""),
        AdbResult(0, typed_comp_xml, ""),
        AdbResult(0, MODERN_FEED_DUMP, ""),
    ]
    ui_seq_iter = iter(ui_seq)
    with (
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof_submit),
        mock.patch("phone_runner.publish.query_ui_hierarchy", side_effect=lambda s: next(ui_seq_iter, AdbResult(0, "", ""))),
        mock.patch("phone_runner.publish.type_text", return_value=MobilerunResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_adb", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.save_composer_observation", return_value="/obs/submit_gate.json") as mock_obs,
    ):
        res_submit = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post text",
        )
    assert res_submit["status"] == "failed"
    assert res_submit["error_code"] == "share_screen_unrecognized"
    mock_obs.assert_called_once_with(TEST_SERIAL, "share_screen_unrecognized")
    assert res_submit["evidence"]["observation_file"] == "/obs/submit_gate.json"


def test_publish_legacy_path_never_calls_observation(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setenv("PHONE_RUNNER_MEDIA_HOSTS", "cdn.example.com")
    monkeypatch.setenv("PHONE_RUNNER_MAX_MEDIA_BYTES", "5242880")

    monkeypatch.setattr("phone_runner.publish.ping_portal", lambda s: AdbResult(0, "pong\n", ""))
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.launch_facebook_feed", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_mobilerun", return_value=MobilerunResult(0, "Navigated", "")),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, LEGACY_COMPOSER_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish.type_text", return_value=MobilerunResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_adb", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.poll_for_post_result", return_value=("12345", "https://fb.com/12345", {})),
        mock.patch("phone_runner.publish.save_composer_observation") as mock_obs,
    ):
            res = execute_publish(
                serial=TEST_SERIAL,
                main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
                destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
                caption="Test caption",
            )

    assert res["status"] == "published"
    mock_obs.assert_not_called()
    assert "observation_file" not in res["evidence"]


def test_publish_early_failure_paths_never_call_observation(monkeypatch):
    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)

    with mock.patch("phone_runner.publish.save_composer_observation") as mock_obs:
        # Missing profile ID
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": "", "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "missing_profile_id"
    mock_obs.assert_not_called()
    assert "observation_file" not in res["evidence"]
