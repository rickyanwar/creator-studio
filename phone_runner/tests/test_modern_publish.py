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


def _make_valid_modern_proof(
    empty_state_certainty: bool | None = False,
    header_variant: str = "actor_title",
    nonce: str = "nonce_123",
) -> IdentityProofResult:
    return IdentityProofResult(
        nonce=nonce,
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
            "empty_state_certainty": empty_state_certainty,
            "header_variant": header_variant,
        },
    )


def test_legacy_full_path_still_works():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.launch_facebook_feed", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_mobilerun", return_value=MobilerunResult(0, "Navigated", "")),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, LEGACY_COMPOSER_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish.type_text", return_value=MobilerunResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_adb", return_value=AdbResult(0, "OK", "")) as mock_adb,
        mock.patch("phone_runner.publish.poll_for_post_result", return_value=("998877665544", "https://fb.com/posts/998877665544", {})),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test caption",
        )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "998877665544"
    assert res["publish_may_have_started"] is True
    tap_calls = [c for c in mock_adb.call_args_list if "tap" in c[0][0]]
    assert len(tap_calls) == 1


def test_legacy_partial_root_only_feed_becomes_both_ids_after_navigation():
    # Initial capture at feed: root ID is present via c_user, but composer is not open yet (no actor in UI)
    feed_ui = AdbResult(0, MODERN_FEED_DUMP, "")
    feed_act = AdbResult(0, LEGACY_SESSION_DUMP, "")
    # Post-navigation capture: composer is open, both IDs match
    composer_ui = AdbResult(0, LEGACY_COMPOSER_DUMP, "")

    ui_side_effects = [feed_ui, composer_ui, composer_ui]

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.launch_facebook_feed", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_mobilerun", return_value=MobilerunResult(0, "Navigated", "")),
        mock.patch("phone_runner.publish.query_ui_hierarchy", side_effect=ui_side_effects),
        mock.patch("phone_runner.publish.query_activity_state", return_value=feed_act),
        mock.patch("phone_runner.publish.type_text", return_value=MobilerunResult(0, "OK", "")),
        mock.patch("phone_runner.publish.run_adb", return_value=AdbResult(0, "OK", "")) as mock_adb,
        mock.patch("phone_runner.publish.poll_for_post_result", return_value=("998877665544", "https://fb.com/posts/998877665544", {})),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test caption",
        )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "998877665544"
    assert res["publish_may_have_started"] is True


def test_candidate_capture_nonzero_ui_fails_safe():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(1, "", "dump error")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain") as mock_collector,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Hello",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_mismatch"
    assert res["publish_may_have_started"] is False
    mock_collector.assert_not_called()


def test_candidate_capture_nonzero_activity_fails_safe():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(1, "", "dumpsys error")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain") as mock_collector,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Hello",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_mismatch"
    assert res["publish_may_have_started"] is False
    mock_collector.assert_not_called()


def test_conflicting_initial_root_id_fails_before_collector_or_staging():
    spoofed_session = "Intent: act=android.intent.action.VIEW c_user=9999999999 cmp=com.facebook.katana/.FeedActivity"
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, spoofed_session, "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain") as mock_collector,
        mock.patch("phone_runner.publish.stage_media_files") as mock_stage,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Hello",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_mismatch"
    assert res["publish_may_have_started"] is False
    mock_collector.assert_not_called()
    mock_stage.assert_not_called()


def test_conflicting_initial_composer_id_fails_before_collector_or_staging():
    spoofed_composer = """
    <hierarchy rotation="0">
      <node resource-id="com.facebook.katana:id/composer_actor" content-desc="fb://profile/888888888" text="Impostor" />
    </hierarchy>
    """
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, spoofed_composer, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain") as mock_collector,
        mock.patch("phone_runner.publish.stage_media_files") as mock_stage,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Hello",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_mismatch"
    assert res["publish_may_have_started"] is False
    mock_collector.assert_not_called()
    mock_stage.assert_not_called()


def test_both_ids_absent_triggers_fresh_collector():
    collector_calls = []
    fake_proof = _make_valid_modern_proof(empty_state_certainty=False)

    def _mock_collector(**kwargs):
        collector_calls.append(kwargs)
        return fake_proof

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", side_effect=_mock_collector),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert len(collector_calls) == 1
    call_kw = collector_calls[0]
    assert call_kw["serial"] == TEST_SERIAL
    assert call_kw["main_name"] == TEST_ROOT_NAME
    assert call_kw["expected_root_id"] == TEST_ROOT_ID
    assert call_kw["target_name"] == TEST_DEST_NAME
    assert call_kw["expected_target_id"] == TEST_DEST_ID
    assert call_kw["timeout"] <= 240.0
    assert res["status"] == "failed"
    assert res["error_code"] == "draft_state_uncertain"


def test_collector_failure_fails_closed():
    failing_proof = _make_valid_modern_proof(empty_state_certainty=False)
    failing_proof.passed = False
    failing_proof.error = "Root navigation timeout"
    failing_proof.stages = [{"stage": "root_url", "passed": False, "detail": "Timeout"}]

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=failing_proof),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"
    assert res["publish_may_have_started"] is False
    assert res["facebook_post_id"] is None
    assert res["permalink"] is None
    assert any(s.get("stage") == "identity_gate" and s.get("passed") is False for s in res["stages"])


def test_collector_exception_fails_sanitized():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch(
            "phone_runner.publish.collect_own_url_proof_chain",
            side_effect=RuntimeError("PRIVATE_KEY_TOKEN_SECRET"),
        ),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"
    assert res["publish_may_have_started"] is False
    # Sanitized type-only diagnostic
    assert "PRIVATE_KEY_TOKEN_SECRET" not in str(res["stages"])
    assert "PRIVATE_KEY_TOKEN_SECRET" not in str(res["evidence"])
    assert any("RuntimeError" in s.get("detail", "") for s in res["stages"])


def test_collector_ime_restoration_failure_fails_closed():
    ime_fail_proof = _make_valid_modern_proof(empty_state_certainty=False)
    ime_fail_proof.passed = False
    ime_fail_proof.error = "IME_RESTORATION_FAILED"

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=ime_fail_proof),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"
    assert res["publish_may_have_started"] is False


@pytest.mark.parametrize(
    "field,value",
    [
        ("observed_root_id", "wrong_id"),
        ("observed_root_id", None),
        ("observed_target_id", "wrong_id"),
        ("observed_target_id", None),
        ("verified_main_profile_id", "wrong_id"),
        ("verified_main_profile_id", None),
        ("active_composer_actor_id", "wrong_id"),
        ("active_composer_actor_id", None),
        ("serial", "other_device:5555"),
        ("method", "arbitrary_method"),
        ("resolution", "720x1280"),
        ("app_card_version", "99.9.9"),
        ("facebook_version", None),
        ("portal_version", None),
    ],
)
def test_proof_passed_true_with_contradictory_field_rejects(field, value):
    bad_proof = _make_valid_modern_proof(empty_state_certainty=True)
    setattr(bad_proof, field, value)

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=bad_proof),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"
    assert res["publish_may_have_started"] is False


@pytest.mark.parametrize("empty_val", [False, None, ""])
def test_actor_title_empty_state_uncertainty_variants_fail_draft_state_uncertain(empty_val):
    proof = _make_valid_modern_proof(empty_state_certainty=empty_val, header_variant="actor_title")
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof),
        mock.patch("phone_runner.publish.type_text") as mock_type,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "draft_state_uncertain"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()


def test_actor_title_empty_state_true_fails_submission_flow_uncalibrated():
    proof = _make_valid_modern_proof(empty_state_certainty=True, header_variant="actor_title")
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof),
        mock.patch("phone_runner.publish.type_text") as mock_type,
        mock.patch("phone_runner.publish.run_adb") as mock_adb,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "submission_flow_uncalibrated"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    tap_calls = [c for c in mock_adb.call_args_list if "tap" in c[0][0]]
    assert len(tap_calls) == 0


def test_modern_media_requests_fail_before_staging():
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof),
        mock.patch("phone_runner.publish.stage_media_files") as mock_stage,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post with media",
            media_urls=["https://cdn.example.com/photo.jpg"],
        )

    assert res["status"] == "failed"
    assert res["error_code"] == "media_unsupported_on_modern"
    assert res["publish_may_have_started"] is False
    mock_stage.assert_not_called()


def test_no_modern_branch_types_clears_or_submits():
    proof = _make_valid_modern_proof(empty_state_certainty=False)
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof),
        mock.patch("phone_runner.publish.type_text") as mock_type,
        mock.patch("phone_runner.publish.run_adb") as mock_adb,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Modern post",
        )

    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    tap_calls = [c for c in mock_adb.call_args_list if "tap" in c[0][0]]
    assert len(tap_calls) == 0


def test_two_invocations_collect_twice_with_unique_nonces():
    proof1 = _make_valid_modern_proof(nonce="nonce_one")
    proof2 = _make_valid_modern_proof(nonce="nonce_two")
    collector_calls = []

    def _mock_collector(**kwargs):
        call_num = len(collector_calls)
        collector_calls.append(kwargs)
        return proof1 if call_num == 0 else proof2

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", side_effect=_mock_collector),
    ):
        res1 = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Post 1",
        )
        res2 = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Post 2",
        )

    assert len(collector_calls) == 2
    assert res1["evidence"]["proof_nonce"] == "nonce_one"
    assert res2["evidence"]["proof_nonce"] == "nonce_two"


def test_asleep_device_restored_on_early_returns_and_exceptions():
    probe_asleep = dict(FAKE_PROBE_READY, screen="asleep")

    # 1. Early return on candidate capture fail
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_asleep),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(1, "", "fail")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.put_to_sleep") as mock_sleep,
    ):
        execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Post",
        )
    mock_sleep.assert_called_once_with(TEST_SERIAL)

    # 2. Early return on draft_state_uncertain
    proof = _make_valid_modern_proof(empty_state_certainty=False)
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_asleep),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, MODERN_FEED_DUMP, "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.collect_own_url_proof_chain", return_value=proof),
        mock.patch("phone_runner.publish.put_to_sleep") as mock_sleep,
    ):
        execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Post",
        )
    mock_sleep.assert_called_once_with(TEST_SERIAL)


def test_sensitive_stderr_and_tokens_never_output():
    dirty_ping = AdbResult(1, "", "DAEMON_SECRET_KEY_12345")
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.ping_portal", return_value=dirty_ping),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Secret caption",
        )

    output_str = str(res)
    assert "DAEMON_SECRET_KEY_12345" not in output_str
    assert "Secret caption" not in output_str


def test_pre_submit_candidate_capture_exception_returns_failed_pre_submit_failed():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", side_effect=RuntimeError("Device capture hardware crash")),
        mock.patch("phone_runner.publish.stage_media_files") as mock_stage,
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test pre-submit failure",
        )

    assert res["status"] == "failed"
    assert res["publish_may_have_started"] is False
    assert res["error_code"] == "pre_submit_failed"
    assert res["facebook_post_id"] is None
    assert res["permalink"] is None
    assert mock_stage.call_count == 0

    err_stage = res["stages"][-1]
    assert err_stage["stage"] == "execution_error"
    assert err_stage["passed"] is False
    assert "RuntimeError" in err_stage["detail"]
    assert "Device capture hardware crash" not in str(res)


def test_pre_submit_legacy_composition_exception_returns_failed_pre_submit_failed():
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish.launch_facebook_feed", side_effect=RuntimeError("Feed launch failed unexpectedly")),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test pre-submit composition crash",
        )

    assert res["status"] == "failed"
    assert res["publish_may_have_started"] is False
    assert res["error_code"] == "pre_submit_failed"
    assert res["facebook_post_id"] is None
    assert res["permalink"] is None

    err_stage = res["stages"][-1]
    assert err_stage["stage"] == "execution_error"
    assert err_stage["passed"] is False
    assert "RuntimeError" in err_stage["detail"]
    assert "Feed launch failed unexpectedly" not in str(res)


def test_pre_submit_exception_with_staged_media_guarantees_cleanup():
    cleanup_called = False

    class FakeStagingCM:
        def __enter__(self):
            return [{"remote_path": "/sdcard/media1.jpg"}]

        def __exit__(self, exc_type, exc_val, exc_tb):
            nonlocal cleanup_called
            cleanup_called = True

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish.stage_media_files", return_value=FakeStagingCM()),
        mock.patch("phone_runner.publish._run_composition_and_publish", side_effect=RuntimeError("Composition crashed after staging")),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test staging cleanup",
            media_urls=["https://cdn.example.com/photo.jpg"],
        )

    assert cleanup_called is True
    assert res["status"] == "failed"
    assert res["publish_may_have_started"] is False
    assert res["error_code"] == "pre_submit_failed"


@pytest.mark.parametrize("corrupt_flag", [True, None, "missing", "unknown_val", 1])
def test_exception_with_submitted_flag_true_or_missing_or_unknown_returns_uncertain(corrupt_flag):
    def _mock_run_composition(**kwargs):
        ev = kwargs.get("evidence", {})
        if corrupt_flag == "missing":
            ev.pop("publish_may_have_started", None)
        else:
            ev["publish_may_have_started"] = corrupt_flag
        raise RuntimeError("Crash inside composition")

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=FAKE_PROBE_READY),
        mock.patch("phone_runner.publish.query_ui_hierarchy", return_value=AdbResult(0, "", "")),
        mock.patch("phone_runner.publish.query_activity_state", return_value=AdbResult(0, LEGACY_SESSION_DUMP, "")),
        mock.patch("phone_runner.publish._run_composition_and_publish", side_effect=_mock_run_composition),
    ):
        res = execute_publish(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test uncertain state crash",
        )

    assert res["status"] == "uncertain"
    assert res["publish_may_have_started"] is True
    assert res["error_code"] == "post_submit_uncertain"
    assert res["facebook_post_id"] is None
    assert res["permalink"] is None
    err_stage = res["stages"][-1]
    assert err_stage["stage"] == "execution_error"
    assert err_stage["passed"] is False
    assert "RuntimeError" in err_stage["detail"]
