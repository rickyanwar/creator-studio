from unittest import mock
import pytest

from phone_runner.adb import AdbResult
from phone_runner.mobilerun import run_mobilerun, type_text

TEST_SERIAL = "100.64.96.50:5555"


@pytest.fixture(autouse=True)
def _default_mock_mobilerun_adb(monkeypatch):
    current_ime = ["com.google.android.inputmethod.latin/.LatinIME"]
    enabled_imes = {"com.google.android.inputmethod.latin/.LatinIME"}

    def fake_adb(args, serial=None):
        if len(args) >= 4 and args[1:3] == ["ime", "set"]:
            current_ime[0] = args[3].strip()
            return AdbResult(0, "OK", "")
        if len(args) >= 4 and args[1:3] == ["ime", "enable"]:
            enabled_imes.add(args[3].strip())
            return AdbResult(0, "OK", "")
        if len(args) >= 4 and args[1:3] == ["ime", "disable"]:
            enabled_imes.discard(args[3].strip())
            return AdbResult(0, "OK", "")
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{current_ime[0]}\n", "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, "\n".join(enabled_imes) + "\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", fake_adb)


def test_run_mobilerun_default_arguments(monkeypatch):
    """Verify default OpenAILike provider, FlashFast model, and api_base."""
    monkeypatch.delenv("MOBILERUN_PROVIDER", raising=False)
    monkeypatch.delenv("MOBILERUN_MODEL", raising=False)
    monkeypatch.delenv("MOBILERUN_API_BASE", raising=False)

    with mock.patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value = mock.Mock(returncode=0, stdout="Success", stderr="")
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt", steps=4)

        assert res.returncode == 0
        cmd = mock_subproc.call_args[0][0]
        assert "--provider" in cmd
        assert cmd[cmd.index("--provider") + 1] == "OpenAILike"
        assert "--api_base" in cmd
        assert cmd[cmd.index("--api_base") + 1] == "http://localhost:20128/v1"
        assert "-m" in cmd
        assert cmd[cmd.index("-m") + 1] == "FlashFast"
        assert "--steps" in cmd
        assert cmd[cmd.index("--steps") + 1] == "4"
        assert "--vision" in cmd
        assert cmd[-1] == "Test prompt"


def test_run_mobilerun_configurable_overrides(monkeypatch):
    """Verify provider, model, and api_base can be overridden via environment."""
    monkeypatch.setenv("MOBILERUN_PROVIDER", "custom_provider")
    monkeypatch.setenv("MOBILERUN_MODEL", "custom_model_v2")
    monkeypatch.setenv("MOBILERUN_API_BASE", "https://custom.router.local/v1")

    with mock.patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value = mock.Mock(returncode=0, stdout="Success", stderr="")
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Override test")

        assert res.returncode == 0
        cmd = mock_subproc.call_args[0][0]
        assert cmd[cmd.index("--provider") + 1] == "custom_provider"
        assert cmd[cmd.index("--api_base") + 1] == "https://custom.router.local/v1"
        assert cmd[cmd.index("-m") + 1] == "custom_model_v2"


def test_run_mobilerun_missing_credentials_fails_closed():
    """Verify runtime credential failure in CLI outputs error and fails closed."""
    with mock.patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value = mock.Mock(
            returncode=1,
            stdout="",
            stderr="Error: No active credentials for provider: openai",
        )
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")

        assert res.returncode == 1
        assert "No active credentials" in res.stderr


def test_type_text_command_arguments():
    """Verify type_text constructs correct arguments with --clear and -- sentinel."""
    with mock.patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        res = type_text(serial=TEST_SERIAL, text="Test input", clear=True)

        assert res.returncode == 0
        cmd = mock_subproc.call_args[0][0]
        assert cmd[1:4] == ["device", "type", "--clear"]
        assert cmd[4:6] == ["-d", TEST_SERIAL]
        assert cmd[6] == "--"
        assert cmd[7] == "Test input"


def test_type_text_without_clear():
    """Verify type_text with clear=False omits --clear."""
    with mock.patch("subprocess.run") as mock_subproc:
        mock_subproc.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        res = type_text(serial=TEST_SERIAL, text="Append input", clear=False)

        assert res.returncode == 0
        cmd = mock_subproc.call_args[0][0]
        assert "--clear" not in cmd
        assert cmd[1:3] == ["device", "type"]
        assert cmd[3:5] == ["-d", TEST_SERIAL]
        assert cmd[5] == "--"
        assert cmd[6] == "Append input"


def test_type_text_invalid_serial():
    """Verify type_text rejects invalid serial format before running subprocess."""
    res = type_text(serial="invalid;rm -rf /", text="test")
    assert res.returncode == -1
    assert "Invalid serial format" in res.stderr


def test_run_mobilerun_restores_ime_on_success(monkeypatch):
    """Verify run_mobilerun restores original default IME and disables Portal on success."""
    adb_commands = []
    def recording_adb(args, serial=None):
        adb_commands.append(list(args))
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", recording_adb)
    with mock.patch("subprocess.run") as mock_sub:
        mock_sub.return_value = mock.Mock(returncode=0, stdout="Success", stderr="")
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")
        assert res.returncode == 0

    set_cmds = [c for c in adb_commands if c[1:3] == ["ime", "set"]]
    assert len(set_cmds) == 1
    assert "com.google.android.inputmethod.latin" in set_cmds[0][3]

    disable_cmds = [c for c in adb_commands if c[1:3] == ["ime", "disable"]]
    assert len(disable_cmds) == 1
    assert "com.mobilerun.portal" in disable_cmds[0][3]


def test_run_mobilerun_restores_ime_on_timeout(monkeypatch):
    """Verify run_mobilerun restores original default IME even when CLI times out."""
    import subprocess
    adb_commands = []
    def recording_adb(args, serial=None):
        adb_commands.append(list(args))
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", recording_adb)
    with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=["mobilerun"], timeout=10)):
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Timeout test")
        assert res.returncode == -1
        assert "timed out" in res.stderr

    set_cmds = [c for c in adb_commands if c[1:3] == ["ime", "set"]]
    assert len(set_cmds) == 1
    assert "com.google.android.inputmethod.latin" in set_cmds[0][3]


def test_run_mobilerun_restores_ime_on_missing_binary(monkeypatch):
    """Verify run_mobilerun restores original default IME when binary is missing."""
    adb_commands = []
    def recording_adb(args, serial=None):
        adb_commands.append(list(args))
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", recording_adb)
    with mock.patch("subprocess.run", side_effect=FileNotFoundError("not found")):
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Missing test")
        assert res.returncode == -1
        assert "not found" in res.stderr

    set_cmds = [c for c in adb_commands if c[1:3] == ["ime", "set"]]
    assert len(set_cmds) == 1


def test_run_mobilerun_preserves_parent_portal(monkeypatch):
    """Verify run_mobilerun preserves incoming Portal selection if parent had already set it."""
    adb_commands = []
    portal_comp = "com.mobilerun.portal/.input.MobilerunKeyboardIME"
    def recording_adb(args, serial=None):
        adb_commands.append(list(args))
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            return AdbResult(0, f"{portal_comp}\n", "")
        if len(args) >= 4 and args[1:4] == ["ime", "list", "-s"]:
            return AdbResult(0, f"{portal_comp}\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", recording_adb)
    with mock.patch("subprocess.run") as mock_sub:
        mock_sub.return_value = mock.Mock(returncode=0, stdout="Success", stderr="")
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")
        assert res.returncode == 0

    disable_cmds = [c for c in adb_commands if c[1:3] == ["ime", "disable"]]
    assert len(disable_cmds) == 0

    set_cmds = [c for c in adb_commands if c[1:3] == ["ime", "set"]]
    assert len(set_cmds) == 1
    assert "com.mobilerun.portal" in set_cmds[0][3]


def test_run_mobilerun_fails_closed_when_snapshot_fails(monkeypatch):
    """Verify run_mobilerun fails closed before running subprocess if IME snapshot fails."""
    def failing_adb(args, serial=None):
        return AdbResult(-1, "", "Device unreachable")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", failing_adb)
    with mock.patch("subprocess.run") as mock_sub:
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")
        assert res.returncode == -1
        assert "Failed to query device IME" in res.stderr
        assert not mock_sub.called


def test_run_mobilerun_fails_closed_when_default_ime_not_real_component(monkeypatch):
    """Verify snapshot fails closed if default IME is null, empty, or lacks component slash."""
    for invalid_val in ["null", "", "none", "unformatted_name"]:
        def invalid_adb(args, serial=None):
            if "default_input_method" in args:
                return AdbResult(0, f"{invalid_val}\n", "")
            return AdbResult(0, "OK", "")

        monkeypatch.setattr("phone_runner.mobilerun.run_adb", invalid_adb)
        with mock.patch("subprocess.run") as mock_sub:
            res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")
            assert res.returncode == -1
            assert "Failed to query device IME" in res.stderr
            assert not mock_sub.called


def test_run_mobilerun_fails_closed_when_ime_list_fails(monkeypatch):
    """Verify snapshot fails closed if ime list command fails nonzero."""
    def list_fail_adb(args, serial=None):
        if "default_input_method" in args:
            return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
        if "list" in args:
            return AdbResult(-1, "", "Permission denied")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", list_fail_adb)
    with mock.patch("subprocess.run") as mock_sub:
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")
        assert res.returncode == -1
        assert "Failed to query device IME" in res.stderr
        assert not mock_sub.called


def test_run_mobilerun_returns_error_when_restoration_readback_fails(monkeypatch):
    """If CLI succeeds but restoring default IME fails readback, returns error and fails closed."""
    get_calls = [0]
    def bad_restore_adb(args, serial=None):
        if len(args) >= 5 and args[1:5] == ["settings", "get", "secure", "default_input_method"]:
            get_calls[0] += 1
            if get_calls[0] == 1:
                # Initial snapshot is LatinIME
                return AdbResult(0, "com.google.android.inputmethod.latin/.LatinIME\n", "")
            # Restoration readback returns wrong IME
            return AdbResult(0, "com.mobilerun.portal/.input.MobilerunKeyboardIME\n", "")
        return AdbResult(0, "OK", "")

    monkeypatch.setattr("phone_runner.mobilerun.run_adb", bad_restore_adb)
    with mock.patch("subprocess.run") as mock_sub:
        mock_sub.return_value = mock.Mock(returncode=0, stdout="CLI success", stderr="")
        res = run_mobilerun(serial=TEST_SERIAL, prompt="Test prompt")
        assert res.returncode == -1
        assert res.stderr == "IME_RESTORATION_FAILED"
