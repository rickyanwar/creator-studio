"""Identity gate: unreadable UI waits for a retry, contradictions stay terminal; Play Store cleanup."""

import pytest

from phone_runner.adb import AdbResult
from phone_runner.publish import _identity_failure_is_ui_state, execute_publish
from phone_runner.tests.test_modern_publish import (
    FAKE_PROBE_READY,
    MODERN_FEED_DUMP,
    TEST_DEST_ID,
    TEST_DEST_NAME,
    TEST_ROOT_ID,
    TEST_ROOT_NAME,
    TEST_SERIAL,
    _make_valid_modern_proof,
)

FOCUS_CMD = ["shell", "dumpsys window | grep mCurrentFocus"]
VENDING_FOCUS = "  mCurrentFocus=Window{4b1c2d u0 com.android.vending/com.google.android.finsky.activities.MainActivity}\n"
FACEBOOK_FOCUS = "  mCurrentFocus=Window{9f3e1a u0 com.facebook.katana/com.facebook.katana.LoginActivity}\n"
SETTINGS_FOCUS = "  mCurrentFocus=Window{1a2b3c u0 com.android.settings/com.android.settings.Settings}\n"

UI_STATE_ERRORS = [
    "Copy profile link button not found after scroll",
    "Root own profile invalid: Expected exact name 'Agung' in package com.facebook.katana not found",
    "Root own profile invalid: Missing Edit profile or More profile settings button on own profile",
    "Root own profile check failed: Contradictory window focus: focus=com.android.vending, app=com.android.vending",
    "Operation timed out: deadline exceeded before root own profile check",
    "Root navigation timeout",
    "IME_RESTORATION_FAILED",
    "Device IME restoration failed after model navigation",
]

TERMINAL_ERRORS = [
    None,
    "",
    "Observed root ID '100000000000001' does not match expected '100012345678901'",
    "Observed Page ID '100000000000002' does not match expected '100098765432109'",
    "Facebook version mismatch: actual '1.0' != expected '2.0'",
    "Profile name mismatch: observed 'Someone Else' not found",
    "Profile id binding failed: Detected visitor profile marker: 'Follow'",
    "Profile id binding failed: Detected visitor profile marker: 'Follow' (Missing owner control)",
    "Device is unreachable via ADB",
    "Proof collector unexpected error: KeyError",
]


def _failed_proof(error):
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    proof.passed = False
    proof.error = error
    return proof


# ── Helper rules ──────────────────────────────────────────────────────────────


def test_helper_treats_collection_exception_as_ui_state():
    assert _identity_failure_is_ui_state(None, RuntimeError("boom")) is True


@pytest.mark.parametrize("error", UI_STATE_ERRORS)
def test_helper_production_ui_errors_are_ui_state(error):
    assert _identity_failure_is_ui_state(_failed_proof(error), None) is True


@pytest.mark.parametrize("error", TERMINAL_ERRORS)
def test_helper_contradictions_and_unknown_errors_stay_terminal(error):
    assert _identity_failure_is_ui_state(_failed_proof(error), None) is False


def test_helper_passed_but_contradictory_proof_stays_terminal():
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    proof.observed_root_id = "wrong_id"
    proof.error = "Copy profile link button not found after scroll"  # a stray UI marker must not matter
    assert _identity_failure_is_ui_state(proof, None) is False


def test_helper_without_proof_or_exception_stays_terminal():
    assert _identity_failure_is_ui_state(None, None) is False


# ── Publish flow ──────────────────────────────────────────────────────────────


@pytest.fixture
def publish_env(monkeypatch):
    """Patches the device layer; returns a call log and a setter for the focus line and the proof."""
    state = {"focus": FACEBOOK_FOCUS, "proof": None, "proof_exc": None, "focus_exc": None}
    calls: list[tuple[str, list[str]]] = []

    def fake_run_adb(cmd, serial=None, timeout=None):
        calls.append(("adb", list(cmd)))
        if list(cmd) == FOCUS_CMD:
            if state["focus_exc"] is not None:
                raise state["focus_exc"]
            return AdbResult(0, state["focus"], "")
        return AdbResult(0, "", "")

    def fake_collect(**kwargs):
        calls.append(("collect", []))
        if state["proof_exc"] is not None:
            raise state["proof_exc"]
        return state["proof"]

    monkeypatch.setenv("PHONE_RUNNER_ALLOWED_SERIALS", TEST_SERIAL)
    monkeypatch.setattr("phone_runner.publish.probe_device", lambda s, wake=False: FAKE_PROBE_READY)
    monkeypatch.setattr("phone_runner.publish.verify_mobilerun_service", lambda s: (True, "pong"))
    monkeypatch.setattr("phone_runner.publish.get_local_app_card_version", lambda: "1.0.0")
    monkeypatch.setattr("phone_runner.publish.wake_and_unlock", lambda s: AdbResult(0, "OK", ""))
    monkeypatch.setattr("phone_runner.publish.is_device_secure_locked", lambda s: False)
    monkeypatch.setattr("phone_runner.publish.put_to_sleep", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.run_adb", fake_run_adb)
    monkeypatch.setattr("phone_runner.publish.query_ui_hierarchy", lambda s: AdbResult(0, MODERN_FEED_DUMP, ""))
    monkeypatch.setattr("phone_runner.publish.query_activity_state", lambda s: AdbResult(0, "", ""))
    monkeypatch.setattr("phone_runner.publish.collect_own_url_proof_chain", fake_collect)
    monkeypatch.setattr("phone_runner.publish.time.sleep", lambda s: None)
    return state, calls


def _publish():
    return execute_publish(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
        caption="Modern post",
    )


def _identity_stage(res):
    return next(s for s in res["stages"] if s.get("stage") == "identity_gate")


def test_publish_copy_link_not_found_waits_for_device(publish_env):
    state, _calls = publish_env
    state["proof"] = _failed_proof("Copy profile link button not found after scroll")

    res = _publish()

    assert res["status"] == "waiting_for_device"
    assert res["error_code"] == "identity_ui_unready"
    assert res["publish_may_have_started"] is False
    assert res["facebook_post_id"] is None
    assert res["evidence"]["publish_may_have_started"] is False
    stage = _identity_stage(res)
    assert stage["passed"] is False
    assert stage["detail"] == "Identity proof verification failed: Copy profile link button not found after scroll"


def test_publish_collection_exception_waits_with_type_only_detail(publish_env):
    state, _calls = publish_env
    state["proof_exc"] = RuntimeError("PRIVATE_TOKEN_SECRET")

    res = _publish()

    assert res["status"] == "waiting_for_device"
    assert res["error_code"] == "identity_ui_unready"
    assert res["publish_may_have_started"] is False
    assert _identity_stage(res)["detail"] == "Identity proof collection failed: RuntimeError"
    assert "PRIVATE_TOKEN_SECRET" not in str(res["stages"])


def test_publish_contradictory_proof_stays_failed(publish_env):
    state, _calls = publish_env
    proof = _make_valid_modern_proof(empty_state_certainty=True)
    proof.observed_root_id = "100000000000001"
    state["proof"] = proof

    res = _publish()

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"
    assert res["publish_may_have_started"] is False
    assert _identity_stage(res)["detail"] == "Identity proof contradictory with probe evidence"


def test_publish_observed_id_mismatch_stays_failed(publish_env):
    state, _calls = publish_env
    state["proof"] = _failed_proof("Observed root ID '100000000000001' does not match expected '100012345678901'")

    res = _publish()

    assert res["status"] == "failed"
    assert res["error_code"] == "identity_unverified"


# ── Play Store cleanup before the identity gate ───────────────────────────────


def _force_stops(calls, package):
    return [i for i, (kind, cmd) in enumerate(calls) if kind == "adb" and cmd == ["shell", "am", "force-stop", package]]


def test_play_store_in_front_is_force_stopped_before_identity_gate(publish_env):
    state, calls = publish_env
    state["focus"] = VENDING_FOCUS
    state["proof"] = _failed_proof("Copy profile link button not found after scroll")

    res = _publish()

    vending_stops = _force_stops(calls, "com.android.vending")
    collect_at = next(i for i, (kind, _cmd) in enumerate(calls) if kind == "collect")
    assert len(vending_stops) == 1
    assert vending_stops[0] < collect_at
    assert {"stage": "foreground_cleanup", "passed": True, "detail": "Closed com.android.vending"} in res["stages"]


@pytest.mark.parametrize("focus", [FACEBOOK_FOCUS, SETTINGS_FOCUS, ""])
def test_no_cleanup_when_play_store_not_in_front(publish_env, focus):
    state, calls = publish_env
    state["focus"] = focus
    state["proof"] = _failed_proof("Copy profile link button not found after scroll")

    res = _publish()

    assert _force_stops(calls, "com.android.vending") == []
    assert _force_stops(calls, "com.android.settings") == []
    assert not any(s.get("stage") == "foreground_cleanup" for s in res["stages"])
    assert any(kind == "collect" for kind, _cmd in calls)


def test_cleanup_error_never_fails_the_post(publish_env):
    state, calls = publish_env
    state["focus_exc"] = RuntimeError("adb exploded")
    state["proof"] = _make_valid_modern_proof(empty_state_certainty=True)
    state["proof"].observed_root_id = "100000000000001"

    res = _publish()

    assert any(kind == "collect" for kind, _cmd in calls)
    assert not any(s.get("stage") == "foreground_cleanup" for s in res["stages"])
    # The run continues to the identity gate, which decides the outcome.
    assert res["error_code"] == "identity_unverified"
