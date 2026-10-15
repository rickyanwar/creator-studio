from unittest import mock

from phone_runner.adb import (
    AdbResult,
    parse_package_version,
    parse_resolution,
    parse_rotation,
    parse_screen_state,
)
from phone_runner.probe import probe_device

TEST_SERIAL = "100.64.96.50:5555"


def fake_adb_router(args_map):
    def _run_adb(args, serial=None, timeout=None):
        cmd_key = " ".join(args)
        for pattern, res in args_map.items():
            if pattern in cmd_key:
                return res
        return AdbResult(returncode=0, stdout="", stderr="")

    return _run_adb


def test_asleep_reachable():
    args_map = {
        "get-state": AdbResult(0, "device\n", ""),
        "dumpsys power": AdbResult(0, "mWakefulness=Asleep\nDisplay Power: state=OFF\n", ""),
        "wm size": AdbResult(0, "Physical size: 1080x2408\n", ""),
        "dumpsys window": AdbResult(0, "mCurrentRotation=ROTATION_0\n", ""),
        "dumpsys package com.facebook.katana": AdbResult(0, "versionName=450.0.0.34.109\n", ""),
        "dumpsys package com.mobilerun.portal": AdbResult(0, "versionName=0.7.25\n", ""),
        "content query --uri content://com.mobilerun.portal/ping": AdbResult(
            0, "Row: 0 result=pong\n", ""
        ),
    }

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_adb_router(args_map)):
        res = probe_device(TEST_SERIAL, wake=False)

    assert res["status"] == "asleep_reachable"
    assert res["adb_connected"] is True
    assert res["portal_ready"] is True
    assert res["screen"] == "asleep"
    assert res["resolution"] == "1080x2408"
    assert res["rotation"] == 0
    assert res["facebook_version"] == "450.0.0.34.109"
    assert res["portal_version"] == "0.7.25"
    assert "checked_at" in res


def test_wake_true_wakes_device():
    calls = []
    power_call_count = [0]

    def _run_adb(args, serial=None, timeout=None):
        cmd_str = " ".join(args)
        calls.append(cmd_str)
        if "get-state" in cmd_str:
            return AdbResult(0, "device\n", "")
        if "dumpsys power" in cmd_str:
            power_call_count[0] += 1
            if power_call_count[0] == 1:
                return AdbResult(0, "mWakefulness=Asleep\n", "")
            return AdbResult(0, "mWakefulness=Awake\nDisplay Power: state=ON\n", "")
        if "wm size" in cmd_str:
            return AdbResult(0, "Physical size: 1080x2408\n", "")
        if "content query" in cmd_str:
            return AdbResult(0, "Row: 0 result=pong\n", "")
        return AdbResult(0, "", "")

    with mock.patch("phone_runner.adb.run_adb", side_effect=_run_adb):
        res = probe_device(TEST_SERIAL, wake=True)

    assert res["status"] == "ready"
    assert res["screen"] == "awake"
    assert any("input keyevent 224" in c for c in calls)
    assert any("wm dismiss-keyguard" in c for c in calls)


def test_wake_false_leaves_sleeping_screen_asleep():
    calls = []

    def _run_adb(args, serial=None, timeout=None):
        cmd_str = " ".join(args)
        calls.append(cmd_str)
        if "get-state" in cmd_str:
            return AdbResult(0, "device\n", "")
        if "dumpsys power" in cmd_str:
            return AdbResult(0, "mWakefulness=Asleep\n", "")
        if "content query" in cmd_str:
            return AdbResult(0, "Row: 0 result=pong\n", "")
        return AdbResult(0, "", "")

    with mock.patch("phone_runner.adb.run_adb", side_effect=_run_adb):
        res = probe_device(TEST_SERIAL, wake=False)

    assert res["status"] == "asleep_reachable"
    assert res["screen"] == "asleep"
    assert not any("keyevent 224" in c for c in calls)
    assert not any("dismiss-keyguard" in c for c in calls)


def test_remote_unreachable():
    args_map = {
        "get-state": AdbResult(1, "", "error: device '100.64.96.50:5555' not found\n"),
    }

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_adb_router(args_map)):
        res = probe_device(TEST_SERIAL, wake=False)

    assert res["status"] == "remote_unreachable"
    assert res["adb_connected"] is False
    assert res["portal_ready"] is False
    assert res["screen"] == "unknown"
    assert res["resolution"] is None


def test_portal_missing():
    args_map = {
        "get-state": AdbResult(0, "device\n", ""),
        "dumpsys power": AdbResult(0, "mWakefulness=Awake\n", ""),
        "wm size": AdbResult(0, "Physical size: 1080x2408\n", ""),
        "content query --uri content://com.mobilerun.portal/ping": AdbResult(
            1, "", "Error: could not query provider\n"
        ),
    }

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_adb_router(args_map)):
        res = probe_device(TEST_SERIAL, wake=False)

    assert res["status"] == "portal_unready"
    assert res["adb_connected"] is True
    assert res["portal_ready"] is False
    assert res["screen"] == "awake"


def test_parsing_helpers():
    assert parse_screen_state("mWakefulness=Awake") == "awake"
    assert parse_screen_state("Display Power: state=ON") == "awake"
    assert parse_screen_state("isAwake=true") == "awake"
    assert parse_screen_state("mWakefulness=Asleep") == "asleep"
    assert parse_screen_state("Display Power: state=OFF") == "asleep"
    assert parse_screen_state("isAwake=false") == "asleep"
    assert parse_screen_state("") == "unknown"

    assert parse_resolution("Physical size: 1080x2408") == "1080x2408"
    assert parse_resolution("Override size: 720x1600\nPhysical size: 1080x2408") == "720x1600"
    assert parse_resolution("garbage") is None

    assert parse_rotation("mCurrentRotation=ROTATION_0") == 0
    assert parse_rotation("mCurrentRotation=ROTATION_90") == 90
    assert parse_rotation("mOrientation=1") == 1
    assert parse_rotation("") == 0

    assert parse_package_version("versionName=450.0.0.34.109") == "450.0.0.34.109"
    assert parse_package_version("no version") is None
