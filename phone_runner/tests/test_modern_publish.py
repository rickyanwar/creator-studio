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
    assert res["error_code"] == "draft_state_uncertain"
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
    assert res["error_code"] == "draft_state_uncertain"
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
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="Hello with modern share intent" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""

POST_SETTINGS_SCREEN_XML = """<hierarchy rotation="0">
  <node text="Post settings" bounds="[200,100][600,200]" />
  <node class="android.widget.Button" content-desc="SHARE" bounds="[811,125][1026,233]" />
  <node class="android.widget.Button" content-desc="Post audience, Public" bounds="[50,300][1030,450]" />
</hierarchy>"""


def test_modern_share_intent_happy_path_published(monkeypatch):
    # (1) happy path -> published, SHARE tapped exactly once, 4 gates in evidence
    proof = _make_valid_modern_proof(empty_state_certainty=True)
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
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    gates_called = []
    def mock_verify_gate(serial, gate, **kw):
        gates_called.append(gate)
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_123", "https://facebook.com/posts/post_123", "resolved"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_123"
    assert res["permalink"] == "https://facebook.com/posts/post_123"
    assert res["publish_may_have_started"] is True
    assert res["error_code"] is None

    # SHARE tapped exactly once
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 1

    # 4 gates in evidence
    vision_gates = res["evidence"]["vision_gates"]
    assert len(vision_gates) == 4
    assert [g["gate"] for g in vision_gates] == [
        "composer_before_typing",
        "composer_after_typing",
        "post_settings",
        "posted",
    ]
    assert all(g["ok"] is True for g in vision_gates)


def test_modern_share_intent_composer_before_typing_gate_fails(monkeypatch):
    # (2) composer_before_typing gate fails -> failed, nothing typed, no SHARE
    proof = _make_valid_modern_proof(empty_state_certainty=True)
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
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    mock_type = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.type_text", mock_type)

    def mock_verify_gate(serial, gate, **kw):
        return False, {"gate": gate, "model": "gemini-flash", "ok": False, "detail": "author mismatch", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "vision_gate_failed"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 0
    assert len(res["evidence"]["vision_gates"]) == 1


def test_modern_share_intent_post_settings_gate_fails(monkeypatch):
    # (3) post_settings gate fails -> failed, no SHARE
    proof = _make_valid_modern_proof(empty_state_certainty=True)
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
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    def mock_verify_gate(serial, gate, **kw):
        if gate == "post_settings":
            return False, {"gate": gate, "model": "gemini-flash", "ok": False, "detail": "share button not visible", "answer": {}}
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "vision_gate_failed"
    assert res["publish_may_have_started"] is False
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 0
    assert len(res["evidence"]["vision_gates"]) == 3


def test_modern_share_intent_posted_gate_fails_uncertain(monkeypatch):
    # (4) posted gate fails -> uncertain, facebook_post_id None, resolved id in evidence
    proof = _make_valid_modern_proof(empty_state_certainty=True)
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
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))

    def mock_verify_gate(serial, gate, **kw):
        if gate == "posted":
            return False, {"gate": gate, "model": "gemini-flash", "ok": False, "detail": "photo not visible on profile", "answer": {}}
        return True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "gate passed", "answer": {}}

    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_verify_gate)
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_uncertain_999", "https://facebook.com/posts/post_uncertain_999", "resolved"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "uncertain"
    assert res["error_code"] == "post_vision_mismatch"
    assert res["publish_may_have_started"] is True
    assert res["facebook_post_id"] is None
    assert res["evidence"]["resolved_post_id"] == "post_uncertain_999"
    assert res["evidence"]["resolved_permalink"] == "https://facebook.com/posts/post_uncertain_999"

    # SHARE was tapped once
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 1
    assert len(res["evidence"]["vision_gates"]) == 4


def test_modern_share_intent_wrong_author_in_share_composer_fails(monkeypatch):
    # (5) wrong author "Agung Baelah" in share composer -> failed before typing
    proof = _make_valid_modern_proof(empty_state_certainty=True)
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
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr(
        "phone_runner.publish.open_share_intent_composer",
        lambda s, u, a, **kw: (False, "wrong author: expected 'OwnPageTarget', observed ['Agung Baelah']", None),
    )
    mock_type = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.type_text", mock_type)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "media_attach_failed"
    assert res["publish_may_have_started"] is False
    mock_type.assert_not_called()
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 0


def test_modern_share_intent_two_media_urls_fails_before_staging(monkeypatch):
    # (6) two media urls -> failed before staging
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_stage = mock.MagicMock()
    monkeypatch.setattr("phone_runner.publish.stage_media_files", mock_stage)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with two media",
        media_urls=["https://cdn.example.com/p1.jpg", "https://cdn.example.com/p2.jpg"],
        media_kind="video",
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "media_attach_failed"
    assert res["publish_may_have_started"] is False
    mock_stage.assert_not_called()
    assert any("albums support photos only" in s.get("detail", "") for s in res["stages"])


def test_modern_text_only_uses_old_path(monkeypatch):
    # (7) text-only post still uses the old path
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    taps = []
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))

    ui_seq = [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML.replace("Hello with modern share intent", "Text only post"), ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ]
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))

    mock_open_share = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", mock_open_share)
    mock_gate = mock.Mock()
    monkeypatch.setattr("phone_runner.publish.verify_gate", mock_gate)
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_text_only", "https://facebook.com/posts/post_text_only", "resolved"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Text only post",
        media_urls=None,
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_text_only"
    assert res["publish_may_have_started"] is True
    mock_open_share.assert_not_called()
    mock_gate.assert_not_called()
    assert res["evidence"]["vision_gates"] == []
    share_taps = [b for b in taps if b == (811, 125, 1026, 233)]
    assert len(share_taps) == 1


def _patch_modern_happy_flow(monkeypatch, proof, taps):
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))
    ui_iter = iter([
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_SHARE_COMPOSER_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ])
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_123", "https://facebook.com/posts/post_123", "resolved"),
    )


def test_name_proof_accepted_for_profile_destination(monkeypatch):
    import dataclasses

    proof = dataclasses.replace(_make_valid_modern_proof(empty_state_certainty=True), method="own_profile_name_v1")
    taps = []
    _patch_modern_happy_flow(monkeypatch, proof, taps)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_123"
    link_stages = [s for s in res["stages"] if s["stage"] == "post_link_resolved"]
    assert link_stages and "post_123" in link_stages[0]["detail"]


def test_name_proof_rejected_for_page_destination(monkeypatch):
    import dataclasses

    proof = dataclasses.replace(_make_valid_modern_proof(empty_state_certainty=True), method="own_profile_name_v1")
    taps = []
    _patch_modern_happy_flow(monkeypatch, proof, taps)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_page"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] != "published"
    assert res["publish_may_have_started"] is False
    assert (811, 125, 1026, 233) not in taps


TYPED_LONG_CAPTION_NO_PHOTO_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,792]" text="A very long caption that pushed the photo down" />
</hierarchy>"""

SWIPED_REVEALED_PHOTO_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""


def test_long_caption_photo_revealed_after_one_swipe(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps = []
    adb_commands = []
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr(
        "phone_runner.publish.run_adb",
        lambda args, serial=None: adb_commands.append(list(args)) or AdbResult(0, "OK", ""),
    )

    ui_iter = iter([
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # typed dump: caption matches, no photo
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # reflow check 1: still no photo
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # reflow check 2
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # reflow check 3
        AdbResult(0, SWIPED_REVEALED_PHOTO_XML, ""),          # after 1st swipe: photo revealed
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ])
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_123", "https://facebook.com/posts/post_123", "resolved"),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
        rehearsal=False,
    )

    assert res["status"] == "published"
    assert res["facebook_post_id"] == "post_123"
    assert res["publish_may_have_started"] is True
    assert res["error_code"] is None

    # Exactly one swipe command recorded
    swipe_cmds = [cmd for cmd in adb_commands if "swipe" in cmd]
    assert len(swipe_cmds) == 1
    assert swipe_cmds[0] == ["shell", "input", "swipe", "540", "912", "540", "300", "400"]

    # content_gate stage detail confirms swipe count
    content_gate_stage = next(s for s in res["stages"] if s["stage"] == "content_gate")
    assert content_gate_stage["passed"] is True
    assert "photo visible after 1 swipes" in content_gate_stage["detail"]


def test_photo_never_visible_after_three_swipes_fails_media_preview_mismatch(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps = []
    adb_commands = []
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr(
        "phone_runner.publish.run_adb",
        lambda args, serial=None: adb_commands.append(list(args)) or AdbResult(0, "OK", ""),
    )

    ui_iter = iter([
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # typed dump
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # reflow check 1
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # reflow check 2
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # reflow check 3
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # swipe 1
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # swipe 2
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),  # swipe 3
    ])
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "media_preview_mismatch"
    assert res["publish_may_have_started"] is False

    # 3 swipe commands executed
    swipe_cmds = [cmd for cmd in adb_commands if "swipe" in cmd]
    assert len(swipe_cmds) == 3

    # No Next tap, no SHARE tap
    assert (810, 96, 1068, 204) not in taps
    assert (811, 125, 1026, 233) not in taps

    content_gate_stage = next(s for s in res["stages"] if s["stage"] == "content_gate")
    assert content_gate_stage["passed"] is False
    assert "after 3 swipes" in content_gate_stage["detail"]


def test_observation_order_save_before_discard(monkeypatch):
    call_order = []
    monkeypatch.setattr(
        "phone_runner.publish.save_composer_observation",
        lambda s, r: call_order.append(("save_obs", r)) or "/tmp/obs.json",
    )
    monkeypatch.setattr(
        "phone_runner.publish.discard_composer",
        lambda s: call_order.append(("discard", s)),
    )
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: AdbResult(0, MODERN_FEED_DUMP, ""))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    proof = _make_valid_modern_proof(empty_state_certainty=False)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello",
        media_urls=None,
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "draft_state_uncertain"
    assert call_order == [("save_obs", "draft_state_uncertain"), ("discard", TEST_SERIAL)]
    assert call_order[0][0] == "save_obs"
    assert call_order[1][0] == "discard"



SWIPED_CHANGED_CAPTION_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,300][1080,700]" text="A very long caption that pushed the photo down glide" />
  <node class="android.widget.ImageView" content-desc="Photo" bounds="[0,891][1080,1971]" />
  <node class="android.widget.Button" content-desc="Remove photo" bounds="[800,891][1000,1000]" />
</hierarchy>"""


def test_caption_changed_while_revealing_photo_fails_before_share(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps = []
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: AdbResult(0, "OK", ""))
    ui_iter = iter([
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),
        AdbResult(0, SWIPED_CHANGED_CAPTION_XML, ""),
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ])
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "failed"
    assert res["error_code"] == "caption_mismatch"
    assert res["publish_may_have_started"] is False
    assert (811, 125, 1026, 233) not in taps


def _patch_long_caption_flow(monkeypatch, ui_seq, adb_handler, taps):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", lambda **kw: proof)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    monkeypatch.setattr("phone_runner.publish.human_pause", lambda *a, **k: 0.0)
    monkeypatch.setattr("phone_runner.publish.human_tap", lambda s, b, **k: taps.append(b))
    monkeypatch.setattr("phone_runner.publish.run_adb", adb_handler)
    ui_iter = iter(ui_seq)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: next(ui_iter, AdbResult(0, "", "")))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    mock_stage = mock.MagicMock()
    mock_stage.__enter__.return_value = [{"remote_path": "/sdcard/Pictures/staging/stage_1.jpg", "local_path": "/tmp/test.jpg"}]
    monkeypatch.setattr("phone_runner.publish.stage_media_files", lambda urls, serial=None: mock_stage)
    monkeypatch.setattr("phone_runner.publish.get_media_content_uri", lambda s, p: "content://media/external/images/media/101")
    monkeypatch.setattr("phone_runner.publish.open_share_intent_composer", lambda s, u, a, **kw: (True, "opened", SHARE_INTENT_COMPOSER_XML))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda serial, text, clear=False, **kw: MobilerunResult(0, "OK", ""))
    monkeypatch.setattr(
        "phone_runner.publish.verify_gate",
        lambda serial, gate, **kw: (True, {"gate": gate, "model": "gemini-flash", "ok": True, "detail": "ok", "answer": {}}),
    )
    monkeypatch.setattr(
        "phone_runner.publish.resolve_latest_post_link",
        lambda s, c, **kw: ("post_123", "https://facebook.com/posts/post_123", "resolved"),
    )


KEEP_EDITING_DIALOG_XML = """<hierarchy rotation="0">
  <node class="android.widget.TextView" bounds="[159,987][921,1060]" text="Discard changes?" />
  <node class="android.widget.Button" bounds="[369,1240][713,1402]" text="KEEP EDITING" />
  <node class="android.widget.Button" bounds="[713,1240][957,1402]" text="DISCARD" />
</hierarchy>"""


def test_keyboard_hidden_reveals_photo_without_swipe(monkeypatch):
    taps, cmds = [], []

    def adb(args, serial=None):
        cmds.append(list(args))
        if args[:3] == ["shell", "dumpsys", "input_method"]:
            back_pressed = ["shell", "input", "keyevent", "4"] in cmds
            return AdbResult(0, "mInputShown=false\n" if back_pressed else "mInputShown=true\n", "")
        return AdbResult(0, "OK", "")

    _patch_long_caption_flow(monkeypatch, [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),
        AdbResult(0, SWIPED_REVEALED_PHOTO_XML, ""),   # dump inside _hide_keyboard_if_shown (no dialog)
        AdbResult(0, SWIPED_REVEALED_PHOTO_XML, ""),   # re-dump after hiding: photo visible
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ], adb, taps)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert ["shell", "input", "keyevent", "4"] in cmds
    assert not [c for c in cmds if "swipe" in c]
    assert (369, 1240, 713, 1402) not in taps and (713, 1240, 957, 1402) not in taps


def test_back_opened_discard_dialog_taps_keep_editing_never_discard(monkeypatch):
    taps, cmds = [], []

    def adb(args, serial=None):
        cmds.append(list(args))
        if args[:3] == ["shell", "dumpsys", "input_method"]:
            return AdbResult(0, "mInputShown=true\n", "")
        return AdbResult(0, "OK", "")

    _patch_long_caption_flow(monkeypatch, [
        AdbResult(0, MODERN_FEED_DUMP, ""),
        AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
        AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, ""),
        AdbResult(0, KEEP_EDITING_DIALOG_XML, ""),     # Back opened the dialog
        AdbResult(0, SWIPED_REVEALED_PHOTO_XML, ""),   # after KEEP EDITING: photo visible
        AdbResult(0, POST_SETTINGS_SCREEN_XML, ""),
    ], adb, taps)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert (369, 1240, 713, 1402) in taps          # KEEP EDITING
    assert (713, 1240, 957, 1402) not in taps      # never DISCARD
    assert res["status"] == "published"


def test_caption_typing_timeout_scales_with_caption_length():
    from phone_runner.publish import _caption_typing_timeout

    assert _caption_typing_timeout("") == 30.0
    assert _caption_typing_timeout("x" * 426) > 60.0   # a real 426-char caption timed out at the old 15 s
    assert _caption_typing_timeout(None) == 30.0


def test_caption_is_pasted_from_clipboard_and_clipboard_is_emptied(monkeypatch):
    import base64

    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps, cmds, typed = [], [], []
    _patch_modern_happy_flow(monkeypatch, proof, taps)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.adb.run_adb", lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda **kw: typed.append(kw) or MobilerunResult(0, "OK", ""))

    caption = "Hello with modern share intent"
    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption=caption,
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["evidence"]["caption_entry"] == "paste"
    assert typed == []  # no slow typing
    clip_sets = [c for c in cmds if "content://com.mobilerun.portal/clipboard/set" in c]
    b64 = base64.b64encode(caption.encode("utf-8")).decode("ascii")
    assert clip_sets[0][-1] == f"text_base64:s:{b64}"
    assert clip_sets[-1][-1] == "text_base64:s:"  # emptied afterwards
    assert ["shell", "input", "keyevent", "279"] in cmds
    assert ["shell", "input", "keyevent", "66"] not in cmds  # no trailing hashtag: no Enter


def test_caption_ending_in_hashtag_gets_one_enter_to_close_suggestions(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps, cmds = [], []
    _patch_modern_happy_flow(monkeypatch, proof, taps)
    monkeypatch.setattr("phone_runner.publish.run_adb", lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.adb.run_adb", lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "", ""))

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent #MotoGP #Motorsport",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    keyevents = [c[-1] for c in cmds if c[:3] == ["shell", "input", "keyevent"]]
    assert keyevents.count("66") == 1
    assert keyevents.index("66") == keyevents.index("279") + 1  # Enter right after the paste
    # The fixture composer XML always holds the plain caption, so the later caption check fails here.
    assert res["publish_may_have_started"] is False


def test_ends_with_tag_token():
    from phone_runner.publish import _ends_with_tag_token

    assert _ends_with_tag_token("Race day #MotoGP")
    assert _ends_with_tag_token("thanks @tsering9393_ ")
    assert not _ends_with_tag_token("Race day #MotoGP today")
    assert not _ends_with_tag_token("")
    assert not _ends_with_tag_token("Price is 5 # of laps")


def test_clipboard_failure_falls_back_to_typing(monkeypatch):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    taps, typed = [], []
    _patch_modern_happy_flow(monkeypatch, proof, taps)
    monkeypatch.setattr("phone_runner.publish.set_device_clipboard_text", lambda s, t: AdbResult(-1, "", "Clipboard set failed"))
    monkeypatch.setattr("phone_runner.publish.type_text", lambda **kw: typed.append(kw) or MobilerunResult(0, "OK", ""))

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Hello with modern share intent",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["status"] == "published"
    assert res["evidence"]["caption_entry"] == "type"
    assert len(typed) == 1 and typed[0]["timeout"] >= 30.0


TALL_CAPTION_BOX_NO_PHOTO_XML = f"""<hierarchy rotation="0">
  <node class="android.widget.Button" bounds="[30,108][138,216]" content-desc="Close" />
  <node class="android.widget.Button" bounds="[810,96][1068,204]" content-desc="Next" enabled="true" />
  <node class="android.view.ViewGroup" bounds="[252,325][424,384]" text="{TEST_DEST_NAME}" />
  <node class="android.widget.AutoCompleteTextView" bounds="[0,690][1080,2100]" text="A very long caption that pushed the photo down" />
</hierarchy>"""


def test_no_swipe_when_caption_box_reaches_screen_bottom(monkeypatch):
    taps, cmds = [], []
    _patch_long_caption_flow(
        monkeypatch,
        [
            AdbResult(0, MODERN_FEED_DUMP, ""),
            AdbResult(0, EMPTY_PROOF_COMPOSER_XML, ""),
            AdbResult(0, TALL_CAPTION_BOX_NO_PHOTO_XML, ""),
            AdbResult(0, TALL_CAPTION_BOX_NO_PHOTO_XML, ""),
            AdbResult(0, TALL_CAPTION_BOX_NO_PHOTO_XML, ""),
            AdbResult(0, TALL_CAPTION_BOX_NO_PHOTO_XML, ""),
        ],
        lambda args, serial=None: cmds.append(list(args)) or AdbResult(0, "OK", ""),
        taps,
    )

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["error_code"] == "media_preview_mismatch"
    assert not [c for c in cmds if "swipe" in c]
    assert (811, 125, 1026, 233) not in taps


def test_keyboard_that_never_closes_fails_without_swipe_or_share(monkeypatch):
    taps, cmds = [], []

    def adb(args, serial=None):
        cmds.append(list(args))
        if args[:3] == ["shell", "dumpsys", "input_method"]:
            return AdbResult(0, "mInputShown=true\n", "")
        return AdbResult(0, "OK", "")

    _patch_long_caption_flow(
        monkeypatch,
        [AdbResult(0, MODERN_FEED_DUMP, ""), AdbResult(0, EMPTY_PROOF_COMPOSER_XML, "")]
        + [AdbResult(0, TYPED_LONG_CAPTION_NO_PHOTO_XML, "")] * 12,
        adb,
        taps,
    )
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)

    res = execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="A very long caption that pushed the photo down",
        media_urls=["https://cdn.example.com/p.jpg"],
    )

    assert res["error_code"] == "media_preview_mismatch"
    assert not [c for c in cmds if "swipe" in c]
    assert cmds.count(["shell", "input", "keyevent", "4"]) == 3
    assert (811, 125, 1026, 233) not in taps
