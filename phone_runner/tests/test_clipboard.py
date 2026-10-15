import base64
import json
from typing import Any
from unittest import mock

import pytest

from phone_runner.adb import (
    AdbResult,
    clear_device_clipboard,
    read_device_clipboard,
)

TEST_SERIAL = "100.64.96.50:5555"


@pytest.fixture(autouse=True)
def guard_no_unmocked_io(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbid_subprocess(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unmocked subprocess execution in clipboard test")

    def forbid_http(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("Unmocked HTTP execution in clipboard test")

    monkeypatch.setattr("subprocess.run", forbid_subprocess)
    monkeypatch.setattr("subprocess.Popen", forbid_subprocess)
    monkeypatch.setattr("urllib.request.urlopen", forbid_http)
    monkeypatch.setattr("urllib.request.OpenerDirector.open", forbid_http)


def test_actual_json_envelope_parsing() -> None:
    valid_url = "https://www.facebook.com/share/AbC123Token/"
    envelope = json.dumps({"status": "success", "result": valid_url})
    portal_stdout = f"Row: 0 result={envelope}\n"

    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, portal_stdout, "")):
        res = read_device_clipboard(TEST_SERIAL)

    assert res.returncode == 0
    assert res.stdout == valid_url
    assert res.stderr == ""


def test_strict_status_and_string_type() -> None:
    # Error status
    err_envelope = json.dumps({"status": "error", "message": "Portal error"})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={err_envelope}", "")):
        res = read_device_clipboard(TEST_SERIAL)
    assert res.returncode == -1
    assert res.stdout == ""
    assert "not successful" in res.stderr

    # Non-string result payload (e.g. integer)
    int_envelope = json.dumps({"status": "success", "result": 12345678})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={int_envelope}", "")):
        res = read_device_clipboard(TEST_SERIAL)
    assert res.returncode == -1
    assert res.stdout == ""
    assert "invalid" in res.stderr

    # Missing result key
    missing_envelope = json.dumps({"status": "success"})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={missing_envelope}", "")):
        res = read_device_clipboard(TEST_SERIAL)
    assert res.returncode == -1
    assert res.stdout == ""

    # Malformed non-JSON envelope
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, "Row: 0 result={status: malformed}", "")):
        res = read_device_clipboard(TEST_SERIAL)
    assert res.returncode == -1
    assert res.stdout == ""
    assert "malformed" in res.stderr

    # Non-object envelope (list)
    list_envelope = json.dumps(["success", "https://www.facebook.com/share/123"])
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={list_envelope}", "")):
        res = read_device_clipboard(TEST_SERIAL)
    assert res.returncode == -1
    assert res.stdout == ""

    # Oversized envelope (>8192 bytes)
    huge_stdout = "Row: 0 result={" + (" " * 9000) + "}"
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, huge_stdout, "")):
        res = read_device_clipboard(TEST_SERIAL)
    assert res.returncode == -1
    assert res.stdout == ""
    assert "oversized" in res.stderr


def test_fresh_different_sentinels() -> None:
    recorded_cmds: list[tuple[list[str], str | None]] = []

    def fake_run_adb(args: list[str], serial: str | None = None, timeout: float | None = None) -> AdbResult:
        recorded_cmds.append((args, serial))
        return AdbResult(0, "", "")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        res1 = clear_device_clipboard(TEST_SERIAL)
        res2 = clear_device_clipboard(TEST_SERIAL)

    assert res1.returncode == 0
    assert res2.returncode == 0
    assert res1.stdout != res2.stdout
    assert res1.stdout.startswith("sentinel_")
    assert res2.stdout.startswith("sentinel_")

    # Verify base64 values in args match return stdout
    b64_val1 = base64.b64encode(res1.stdout.encode("utf-8")).decode("ascii")
    b64_val2 = base64.b64encode(res2.stdout.encode("utf-8")).decode("ascii")

    assert recorded_cmds[0][0][-1] == f"text_base64:s:{b64_val1}"
    assert recorded_cmds[1][0][-1] == f"text_base64:s:{b64_val2}"


def test_exact_safe_argv_and_serial_binding() -> None:
    recorded_calls: list[tuple[list[str], str | None]] = []

    def fake_run_adb(args: list[str], serial: str | None = None, timeout: float | None = None) -> AdbResult:
        recorded_calls.append((args, serial))
        return AdbResult(0, 'Row: 0 result={"status":"success","result":"sentinel_0123456789abcdef"}', "")

    with mock.patch("phone_runner.adb.run_adb", side_effect=fake_run_adb):
        clear_res = clear_device_clipboard(TEST_SERIAL, sentinel="sentinel_custom_token_1234")
        read_res = read_device_clipboard(TEST_SERIAL)

    assert clear_res.returncode == 0
    assert clear_res.stdout == "sentinel_custom_token_1234"
    assert read_res.returncode == 0

    assert len(recorded_calls) == 2

    # Clear call check
    clear_args, clear_serial = recorded_calls[0]
    assert clear_serial == TEST_SERIAL
    assert clear_args == [
        "shell",
        "content",
        "insert",
        "--uri",
        "content://com.mobilerun.portal/clipboard/set",
        "--bind",
        f"text_base64:s:{base64.b64encode(b'sentinel_custom_token_1234').decode('ascii')}",
    ]

    # Read call check
    read_args, read_serial = recorded_calls[1]
    assert read_serial == TEST_SERIAL
    assert read_args == [
        "shell",
        "content",
        "query",
        "--uri",
        "content://com.mobilerun.portal/clipboard/get",
    ]


def test_get_and_set_failures_fail_closed() -> None:
    # Subprocess execution failure
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(-1, "", "ADB error")):
        clear_fail = clear_device_clipboard(TEST_SERIAL)
        read_fail = read_device_clipboard(TEST_SERIAL)

    assert clear_fail.returncode == -1
    assert clear_fail.stdout == ""
    assert read_fail.returncode == -1
    assert read_fail.stdout == ""

    # Portal keyboard inactive failure
    keyboard_err = "Clipboard read requires Mobilerun Keyboard to be selected"
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, keyboard_err, "")):
        read_kb_fail = read_device_clipboard(TEST_SERIAL)

    assert read_kb_fail.returncode == -1
    assert read_kb_fail.stdout == ""
    assert read_kb_fail.stderr == "Portal keyboard not active"


def test_stale_sentinel_response() -> None:
    sentinel = "sentinel_fedcba9876543210"
    envelope = json.dumps({"status": "success", "result": sentinel})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={envelope}", "")):
        res = read_device_clipboard(TEST_SERIAL, expected_sentinel=sentinel)

    assert res.returncode == 0
    assert res.stdout == sentinel


def test_unknown_content_withheld_no_leak() -> None:
    secret_pass = "super_secret_user_password_999!"
    envelope = json.dumps({"status": "success", "result": secret_pass})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={envelope}", "")):
        res = read_device_clipboard(TEST_SERIAL)

    assert res.returncode == -1
    assert res.stdout == ""
    assert res.stderr == "Clipboard content withheld"
    assert secret_pass not in res.stderr
    assert secret_pass not in res.stdout

    # Non-allowlisted external URL
    external_url = "https://evil.example.com/steal-credentials"
    ext_envelope = json.dumps({"status": "success", "result": external_url})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={ext_envelope}", "")):
        res_ext = read_device_clipboard(TEST_SERIAL)

    assert res_ext.returncode == -1
    assert res_ext.stdout == ""
    assert external_url not in res_ext.stderr

    # Bare numeric string must be withheld as unallowed URL proof
    bare_numeric = "100012345678901"
    num_envelope = json.dumps({"status": "success", "result": bare_numeric})
    with mock.patch("phone_runner.adb.run_adb", return_value=AdbResult(0, f"Row: 0 result={num_envelope}", "")):
        res_num = read_device_clipboard(TEST_SERIAL)

    assert res_num.returncode == -1
    assert res_num.stdout == ""
    assert res_num.stderr == "Clipboard content withheld"
