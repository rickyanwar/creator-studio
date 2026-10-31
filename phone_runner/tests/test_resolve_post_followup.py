from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.publish import resolve_post_followup

TEST_SERIAL = "100.64.96.50:5555"
TEST_ROOT_ID = "100009439229483"
TEST_ROOT_NAME = "Ricky Anwar"
TEST_DEST_ID = "100009439229483"
TEST_DEST_NAME = "Ricky Anwar"
TEST_PAGE_ID = "200000000000001"


def test_resolve_post_followup_found_closes_app_and_restores_sleep():
    probe_data = {"reachable": True, "screen": "asleep", "resolution": "1080x2408"}
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_data),
        mock.patch("phone_runner.publish.wake_and_unlock", return_value=AdbResult(0, "OK", "")) as mock_wake,
        mock.patch("phone_runner.publish.is_device_secure_locked", return_value=False),
        mock.patch(
            "phone_runner.publish.resolve_latest_post_link",
            return_value=(
                "4743496072641653",
                f"https://www.facebook.com/permalink.php?story_fbid=4743496072641653&id={TEST_DEST_ID}",
                "found post link on profile",
            ),
        ) as mock_resolve,
        mock.patch("phone_runner.publish.run_adb", side_effect=record_adb),
        mock.patch("phone_runner.publish.put_to_sleep") as mock_put_to_sleep,
    ):
        res = resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test Caption #motorsport",
            media_kind="video",
            max_seconds=150,
            close_app_after=False,
        )

    assert res["status"] == "found"
    assert res["facebook_post_id"] == "4743496072641653"
    assert res["permalink"] == f"https://www.facebook.com/permalink.php?story_fbid=4743496072641653&id={TEST_DEST_ID}"
    assert res["actor_id"] == TEST_DEST_ID
    assert "found post link on profile" in res["detail"]

    mock_wake.assert_called_once_with(TEST_SERIAL)
    mock_resolve.assert_called_once()
    resolve_kwargs = mock_resolve.call_args.kwargs
    assert resolve_kwargs.get("serial") == TEST_SERIAL
    assert resolve_kwargs.get("caption") == "Test Caption #motorsport"
    assert resolve_kwargs.get("attempts") == 3
    assert resolve_kwargs.get("wait") == 10.0

    # Facebook was force-stopped because post was found
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 1
    assert force_stops[0] == ["shell", "am", "force-stop", "com.facebook.katana"]

    # Stage app_closed was appended
    app_closed_stage = next((s for s in res.get("stages", []) if s.get("stage") == "app_closed"), None)
    assert app_closed_stage is not None
    assert app_closed_stage["passed"] is True

    # Sleep restored because device was asleep at entry
    mock_put_to_sleep.assert_called_once_with(TEST_SERIAL)


def test_resolve_post_followup_not_found_close_app_false_leaves_app_open():
    probe_data = {"reachable": True, "screen": "awake", "resolution": "1080x2408"}
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_data),
        mock.patch("phone_runner.publish.wake_and_unlock", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.is_device_secure_locked", return_value=False),
        mock.patch(
            "phone_runner.publish.resolve_latest_post_link",
            return_value=(None, None, "post not found on profile after 3 attempts"),
        ),
        mock.patch("phone_runner.publish.run_adb", side_effect=record_adb),
        mock.patch("phone_runner.publish.put_to_sleep") as mock_put_to_sleep,
    ):
        res = resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test Caption #motorsport",
            media_kind="video",
            max_seconds=150,
            close_app_after=False,
        )

    assert res["status"] == "not_found"
    assert "post not found on profile" in res["detail"]

    # Facebook was NOT force-stopped
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 0

    # No app_closed stage
    app_closed_stage = next((s for s in res.get("stages", []) if s.get("stage") == "app_closed"), None)
    assert app_closed_stage is None

    # Device was awake at start -> put_to_sleep not called
    mock_put_to_sleep.assert_not_called()


def test_resolve_post_followup_not_found_close_app_true_closes_app():
    probe_data = {"reachable": True, "screen": "asleep", "resolution": "1080x2408"}
    adb_commands = []

    def record_adb(cmd, serial=None, timeout=None):
        adb_commands.append(list(cmd))
        return AdbResult(0, "OK", "")

    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_data),
        mock.patch("phone_runner.publish.wake_and_unlock", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.is_device_secure_locked", return_value=False),
        mock.patch(
            "phone_runner.publish.resolve_latest_post_link",
            return_value=(None, None, "post not found on profile after 3 attempts"),
        ),
        mock.patch("phone_runner.publish.run_adb", side_effect=record_adb),
        mock.patch("phone_runner.publish.put_to_sleep") as mock_put_to_sleep,
    ):
        res = resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test Caption #motorsport",
            media_kind="video",
            max_seconds=150,
            close_app_after=True,
        )

    assert res["status"] == "not_found"

    # Facebook force-stopped because close_app_after=True
    force_stops = [cmd for cmd in adb_commands if cmd[:3] == ["shell", "am", "force-stop"]]
    assert len(force_stops) == 1

    app_closed_stage = next((s for s in res.get("stages", []) if s.get("stage") == "app_closed"), None)
    assert app_closed_stage is not None
    assert app_closed_stage["passed"] is True

    # Sleep restored
    mock_put_to_sleep.assert_called_once_with(TEST_SERIAL)


def test_resolve_post_followup_secure_locked_returns_error_and_restores_sleep():
    probe_data = {"reachable": True, "screen": "asleep", "resolution": "1080x2408"}
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_data),
        mock.patch("phone_runner.publish.wake_and_unlock", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.is_device_secure_locked", return_value=True),
        mock.patch("phone_runner.publish.resolve_latest_post_link") as mock_resolve,
        mock.patch("phone_runner.publish.put_to_sleep") as mock_put_to_sleep,
    ):
        res = resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Test Caption",
        )

    assert res["status"] == "error"
    assert "PIN/Password/Pattern" in res["detail"]
    mock_resolve.assert_not_called()
    mock_put_to_sleep.assert_called_once_with(TEST_SERIAL)


def test_resolve_post_followup_never_calls_share_or_post():
    probe_data = {"reachable": True, "screen": "awake", "resolution": "1080x2408"}
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_data),
        mock.patch("phone_runner.publish.wake_and_unlock", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.is_device_secure_locked", return_value=False),
        mock.patch("phone_runner.publish.human_tap") as mock_tap,
        mock.patch("phone_runner.publish.type_text") as mock_type,
        mock.patch("phone_runner.publish.open_share_intent_composer") as mock_share_intent,
        mock.patch("phone_runner.publish.run_mobilerun") as mock_mobilerun,
        mock.patch("phone_runner.publish.run_adb", return_value=AdbResult(0, "OK", "")),
        mock.patch(
            "phone_runner.publish.resolve_latest_post_link",
            return_value=("post_1", "https://fb.com/permalink.php?story_fbid=post_1&id=1001", "found"),
        ),
    ):
        res = resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="Read-only test caption",
        )

    assert res["status"] == "found"
    mock_tap.assert_not_called()
    mock_type.assert_not_called()
    mock_share_intent.assert_not_called()
    mock_mobilerun.assert_not_called()


def test_resolve_post_followup_unsupported_destination_kind():
    res = resolve_post_followup(
        serial=TEST_SERIAL,
        main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
        destination={"id": "group_123", "name": "FB Group", "kind": "facebook_group"},
        caption="Group post",
    )
    assert res["status"] == "unsupported"
    assert "facebook_group" in res["detail"]


def test_resolve_post_followup_clamps_max_seconds():
    probe_data = {"reachable": True, "screen": "awake", "resolution": "1080x2408"}
    with (
        mock.patch("phone_runner.publish.probe_device", return_value=probe_data),
        mock.patch("phone_runner.publish.wake_and_unlock", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.is_device_secure_locked", return_value=False),
        mock.patch("phone_runner.publish.run_adb", return_value=AdbResult(0, "OK", "")),
        mock.patch("phone_runner.publish.resolve_latest_post_link", return_value=(None, None, "not found")) as mock_resolve,
    ):
        # 1. Underflow: max_seconds=10 clamped to 30
        resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="test",
            max_seconds=10,
        )
        assert mock_resolve.call_count == 1
        deadline1 = mock_resolve.call_args[1]["deadline"]

        # 2. Overflow: max_seconds=999 clamped to 240
        resolve_post_followup(
            serial=TEST_SERIAL,
            main_profile={"id": TEST_ROOT_ID, "name": TEST_ROOT_NAME},
            destination={"id": TEST_DEST_ID, "name": TEST_DEST_NAME, "kind": "facebook_profile"},
            caption="test",
            max_seconds=999,
        )
        assert mock_resolve.call_count == 2
        deadline2 = mock_resolve.call_args[1]["deadline"]

    import time
    now = time.monotonic()
    assert 25 <= (deadline1 - now) <= 35
    assert 235 <= (deadline2 - now) <= 245
