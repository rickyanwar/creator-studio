"""Sleeping identity screens get one wake attempt; contradictions remain terminal."""

from unittest.mock import Mock
import xml.etree.ElementTree as ET

import pytest

from phone_runner import identity_proof
from phone_runner.adb import AdbResult
from phone_runner.publish import _identity_failure_is_ui_state
from phone_runner.tests.test_identity_proof import (
    ROOT_PROFILE_XML,
    TEST_SERIAL,
    guard_no_unmocked_io,
    mock_device_env,
)
from phone_runner.tests.test_identity_ui_retry import _failed_proof

AWAKE = AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON", "")
ASLEEP = AdbResult(0, "mWakefulness=Asleep\nDisplay Power: state=OFF", "")


@pytest.fixture
def wake_env(mock_device_env, monkeypatch):
    wake = Mock(return_value=AdbResult(0, "OK", ""))
    sleep = Mock()
    hierarchy = Mock(return_value=AdbResult(0, ROOT_PROFILE_XML, ""))
    monkeypatch.setattr(identity_proof, "wake_and_unlock", wake)
    monkeypatch.setattr(identity_proof.time, "sleep", sleep)
    monkeypatch.setattr(identity_proof, "query_ui_hierarchy", hierarchy)
    return wake, sleep, hierarchy


def test_sleeping_screen_wakes_once_and_runs_normal_checks(wake_env, monkeypatch):
    wake, sleep, hierarchy = wake_env
    expected = identity_proof.verify_foreground_facebook(TEST_SERIAL, "1080x2408")
    assert expected[0] is True
    wake.assert_not_called()
    hierarchy.reset_mock()
    power = Mock(side_effect=[ASLEEP, AWAKE])
    monkeypatch.setattr(identity_proof, "query_power_state", power)

    result = identity_proof.verify_foreground_facebook(TEST_SERIAL, "1080x2408")

    wake.assert_called_once_with(TEST_SERIAL)
    sleep.assert_called_once_with(1.5)
    assert power.call_count == 2
    hierarchy.assert_called_once_with(TEST_SERIAL)
    assert result[:2] == expected[:2]
    assert ET.tostring(result[2]) == ET.tostring(expected[2])
    assert result[3] == expected[3]


def test_screen_still_asleep_after_one_wake_fails(wake_env, monkeypatch):
    wake, sleep, hierarchy = wake_env
    power = Mock(side_effect=[ASLEEP, ASLEEP])
    monkeypatch.setattr(identity_proof, "query_power_state", power)

    assert identity_proof.verify_foreground_facebook(TEST_SERIAL) == (
        False, "Device is not awake", None, ""
    )
    wake.assert_called_once_with(TEST_SERIAL)
    sleep.assert_called_once_with(1.5)
    assert power.call_count == 2
    hierarchy.assert_not_called()


def test_rewoken_screen_still_fails_secure_lock_check(wake_env, monkeypatch):
    wake, _sleep, hierarchy = wake_env
    monkeypatch.setattr(identity_proof, "query_power_state", Mock(side_effect=[ASLEEP, AWAKE]))
    monkeypatch.setattr(identity_proof, "is_device_secure_locked", lambda s: True)

    assert identity_proof.verify_foreground_facebook(TEST_SERIAL) == (
        False, "Device is secure locked", None, ""
    )
    wake.assert_called_once_with(TEST_SERIAL)
    hierarchy.assert_not_called()


@pytest.mark.parametrize("error, expected", [
    ("Root own profile check failed: Device is not awake", True),
    ("Deadline exceeded before root model navigation", True),
    ("Deadline exceeded: observed id 123 does not match expected id 456", False),
])
def test_identity_wake_and_deadline_errors_retry_unless_contradictory(error, expected):
    assert _identity_failure_is_ui_state(_failed_proof(error), None) is expected
