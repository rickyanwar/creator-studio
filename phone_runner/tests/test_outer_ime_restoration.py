"""Targeted tests for checked outer PortalImeContext restoration.

Verifies:
- Snapshot preservation and exact restoration of default IME and enabled state.
- Enable before set when incoming Portal was enabled but child disabled it.
- Partial __enter__ failure restoration and preservation of original enter exceptions.
- Fail closed with fixed sanitized RuntimeError("Device IME restoration failed") on any failure.
- No leakage of private tokens/exceptions.
- Cancellation of pending successful return when __exit__ restoration fails.
"""

from typing import Any
import pytest

from phone_runner.adb import AdbResult
from phone_runner.identity_proof import PortalImeContext

TEST_SERIAL = "TEST_SERIAL_OUTER_IME_999"
GBOARD_IME = "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
PORTAL_IME = "com.mobilerun.portal/.input.MobilerunKeyboardIME"


class StatefulFakeAdb:
    """Stateful fake ADB runner for IME testing.

    Unknown external IO raises RuntimeError immediately.
    """

    def __init__(
        self,
        default_ime: str | None = GBOARD_IME,
        enabled_imes: list[str] | None = None,
        installed_imes: list[str] | None = None,
        fail_rules: dict[str, Any] | None = None,
    ):
        self.default_ime = default_ime
        self.enabled_imes = set(enabled_imes) if enabled_imes is not None else {GBOARD_IME}
        self.installed_imes = set(installed_imes) if installed_imes is not None else {GBOARD_IME, PORTAL_IME}
        self.fail_rules = fail_rules or {}
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str], serial: str | None = None, timeout: float | None = None) -> AdbResult:
        cmd = list(args)
        self.calls.append(cmd)
        cmd_str = " ".join(cmd)

        for rule_key, outcome in list(self.fail_rules.items()):
            if rule_key in cmd_str:
                if isinstance(outcome, Exception):
                    raise outcome
                if isinstance(outcome, AdbResult):
                    return outcome
                if callable(outcome):
                    return outcome(cmd)

        if cmd == ["shell", "settings", "get", "secure", "default_input_method"]:
            if self.default_ime is None:
                return AdbResult(0, "null\n", "")
            return AdbResult(0, f"{self.default_ime}\n", "")

        if cmd == ["shell", "ime", "list", "-s"]:
            return AdbResult(0, "\n".join(sorted(self.enabled_imes)) + "\n", "")

        if cmd == ["shell", "ime", "list", "-a", "-s"]:
            return AdbResult(0, "\n".join(sorted(self.installed_imes)) + "\n", "")

        if len(cmd) == 4 and cmd[:3] == ["shell", "ime", "enable"]:
            target = cmd[3]
            self.enabled_imes.add(target)
            return AdbResult(0, f"Input method {target} enabled\n", "")

        if len(cmd) == 4 and cmd[:3] == ["shell", "ime", "disable"]:
            target = cmd[3]
            self.enabled_imes.discard(target)
            return AdbResult(0, f"Input method {target} disabled\n", "")

        if len(cmd) == 4 and cmd[:3] == ["shell", "ime", "set"]:
            target = cmd[3]
            self.default_ime = target
            return AdbResult(0, f"Input method {target} selected\n", "")

        raise RuntimeError(f"Unexpected external IO in test: {cmd}")


def test_unexpected_external_io_raises(monkeypatch):
    """Safety check: any unhandled external command raises immediately."""
    fake = StatefulFakeAdb()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    with pytest.raises(RuntimeError) as exc_info:
        fake(["shell", "pm", "list", "packages"])
    assert "Unexpected external IO in test" in str(exc_info.value)


def test_normal_gboard_portal_disabled_restores_exact(monkeypatch):
    """Normal lifecycle: Gboard default, Portal disabled initially.

    Restores original default Gboard and disables Portal IME upon exit.
    """
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    with PortalImeContext(TEST_SERIAL) as ctx:
        assert ctx.original_default == GBOARD_IME
        assert ctx.portal_ime_id == PORTAL_IME
        assert ctx.portal_was_enabled is False
        assert fake.default_ime == PORTAL_IME
        assert PORTAL_IME in fake.enabled_imes

    # After exit: restored
    assert fake.default_ime == GBOARD_IME
    assert PORTAL_IME not in fake.enabled_imes
    assert GBOARD_IME in fake.enabled_imes

    # Verify exit calls executed in checked order
    exit_calls = fake.calls[6:]  # enter has 6 calls: get, list -s, list -a -s, enable, set, get
    assert exit_calls[0] == ["shell", "ime", "set", GBOARD_IME]
    assert exit_calls[1] == ["shell", "settings", "get", "secure", "default_input_method"]
    assert exit_calls[2] == ["shell", "ime", "disable", PORTAL_IME]
    assert exit_calls[3] == ["shell", "ime", "list", "-s"]


def test_original_portal_enabled_child_disabled_enables_before_set(monkeypatch):
    """Incoming Portal enabled and default. Child disables it during execution.

    Restoration must re-enable Portal BEFORE restoring default, and preserve enabled state.
    """
    fake = StatefulFakeAdb(
        default_ime=PORTAL_IME,
        enabled_imes=[PORTAL_IME, GBOARD_IME],
        installed_imes=[PORTAL_IME, GBOARD_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    with PortalImeContext(TEST_SERIAL) as ctx:
        assert ctx.portal_was_enabled is True
        assert ctx.original_default == PORTAL_IME
        # Child disables Portal IME during task execution
        fake.enabled_imes.remove(PORTAL_IME)
        assert PORTAL_IME not in fake.enabled_imes

    # After exit: re-enabled and restored as default
    assert PORTAL_IME in fake.enabled_imes
    assert fake.default_ime == PORTAL_IME

    # Check order of operations during exit: enable MUST come before ime set
    enable_idx = None
    set_idx = None
    for i, c in enumerate(fake.calls[5:]):  # after enter
        if c == ["shell", "ime", "enable", PORTAL_IME] and enable_idx is None:
            enable_idx = i
        if c == ["shell", "ime", "set", PORTAL_IME] and set_idx is None:
            set_idx = i

    assert enable_idx is not None, "ime enable not called during restoration"
    assert set_idx is not None, "ime set not called during restoration"
    assert enable_idx < set_idx, "ime enable must be called BEFORE ime set"


def test_partial_enter_failed_set_restores_known_snapshot(monkeypatch):
    """If __enter__ fails after finding portal_ime_id (e.g. at ime set), exit cleans up and preserves error."""
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
        fail_rules={
            "shell ime set com.mobilerun.portal": AdbResult(1, "", "Failed to set IME"),
        },
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    with pytest.raises(RuntimeError) as exc_info:
        with PortalImeContext(TEST_SERIAL):
            pass

    assert "Failed to set Portal IME" in str(exc_info.value)
    # Original default restored and Portal disabled
    assert fake.default_ime == GBOARD_IME
    assert PORTAL_IME not in fake.enabled_imes


def test_partial_enter_failed_readback_restores_known_snapshot(monkeypatch):
    """If __enter__ fails at readback check, exit cleans up and original error is preserved."""
    readback_count = 0

    def mock_readback(cmd: list[str]) -> AdbResult:
        nonlocal readback_count
        readback_count += 1
        if readback_count == 1:
            # enter query original default
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        if readback_count == 2:
            # enter verify portal readback -> mismatch!
            return AdbResult(0, f"{GBOARD_IME}\n", "")
        # exit verify restoration readback -> success
        return AdbResult(0, f"{GBOARD_IME}\n", "")

    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
        fail_rules={
            "shell settings get secure default_input_method": mock_readback,
        },
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    with pytest.raises(RuntimeError) as exc_info:
        with PortalImeContext(TEST_SERIAL):
            pass

    assert "Portal IME readback mismatch" in str(exc_info.value)
    # Original state cleanly restored
    assert fake.default_ime == GBOARD_IME
    assert PORTAL_IME not in fake.enabled_imes


def test_partial_enter_no_portal_id_restores_known_default(monkeypatch):
    """If __enter__ fails before discovering portal_ime_id, restores known default without guessing components."""
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME],  # Portal NOT installed
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    with pytest.raises(RuntimeError) as exc_info:
        with PortalImeContext(TEST_SERIAL):
            pass

    assert "Portal IME component not installed or not found on device" in str(exc_info.value)
    assert fake.default_ime == GBOARD_IME
    # Ensure no disable command was guessed/issued for unknown components
    disable_calls = [c for c in fake.calls if len(c) > 2 and c[1:3] == ["ime", "disable"]]
    assert len(disable_calls) == 0


@pytest.mark.parametrize(
    "fail_trigger,fail_outcome",
    [
        ("shell ime set " + GBOARD_IME, AdbResult(1, "", "Failed to set IME")),
        ("shell ime set " + GBOARD_IME, RuntimeError("Subprocess failure during set")),
        ("shell settings get secure default_input_method", AdbResult(1, "", "Settings query error")),
        ("shell settings get secure default_input_method", AdbResult(0, "com.other.ime/.OtherIME\n", "")),
        ("shell ime disable " + PORTAL_IME, AdbResult(1, "", "Failed to disable")),
        ("shell ime disable " + PORTAL_IME, RuntimeError("Subprocess error during disable")),
        ("shell ime list -s", AdbResult(1, "", "Failed to query list")),
        ("shell ime list -s", RuntimeError("Subprocess error during list query")),
    ],
)
def test_restoration_failure_modes_all_raise_fixed_sanitized(fail_trigger, fail_outcome, monkeypatch):
    """Every failure mode during restoration raises fixed sanitized RuntimeError with cause suppressed."""
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    ctx = PortalImeContext(TEST_SERIAL)
    ctx.__enter__()

    # Inject failure ONLY for exit phase
    fake.fail_rules[fail_trigger] = fail_outcome

    with pytest.raises(RuntimeError) as exc_info:
        ctx.__exit__(None, None, None)

    assert str(exc_info.value) == "Device IME restoration failed"
    assert exc_info.value.__cause__ is None


def test_restoration_enable_failure_raises_fixed_sanitized(monkeypatch):
    """When portal_was_enabled=True, failure to re-enable portal raises fixed sanitized error."""
    fake = StatefulFakeAdb(
        default_ime=PORTAL_IME,
        enabled_imes=[PORTAL_IME, GBOARD_IME],
        installed_imes=[PORTAL_IME, GBOARD_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    ctx = PortalImeContext(TEST_SERIAL)
    ctx.__enter__()

    # Inject enable failure for exit
    fake.fail_rules["shell ime enable " + PORTAL_IME] = AdbResult(1, "", "Enable failed")

    with pytest.raises(RuntimeError) as exc_info:
        ctx.__exit__(None, None, None)

    assert str(exc_info.value) == "Device IME restoration failed"
    assert exc_info.value.__cause__ is None


def test_restoration_mismatched_membership_still_enabled_raises(monkeypatch):
    """If portal_was_enabled=False, but final list -s still contains portal, raises fixed sanitized error."""
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    ctx = PortalImeContext(TEST_SERIAL)
    ctx.__enter__()

    # Simulate portal remaining enabled despite disable command
    def corrupt_list(cmd: list[str]) -> AdbResult:
        return AdbResult(0, f"{GBOARD_IME}\n{PORTAL_IME}\n", "")

    fake.fail_rules["shell ime list -s"] = corrupt_list

    with pytest.raises(RuntimeError) as exc_info:
        ctx.__exit__(None, None, None)

    assert str(exc_info.value) == "Device IME restoration failed"
    assert exc_info.value.__cause__ is None


def test_restoration_mismatched_membership_missing_enabled_raises(monkeypatch):
    """If portal_was_enabled=True, but final list -s is missing portal, raises fixed sanitized error."""
    fake = StatefulFakeAdb(
        default_ime=PORTAL_IME,
        enabled_imes=[PORTAL_IME, GBOARD_IME],
        installed_imes=[PORTAL_IME, GBOARD_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    ctx = PortalImeContext(TEST_SERIAL)
    ctx.__enter__()

    # Simulate portal missing from enabled list
    def corrupt_list(cmd: list[str]) -> AdbResult:
        return AdbResult(0, f"{GBOARD_IME}\n", "")

    fake.fail_rules["shell ime list -s"] = corrupt_list

    with pytest.raises(RuntimeError) as exc_info:
        ctx.__exit__(None, None, None)

    assert str(exc_info.value) == "Device IME restoration failed"
    assert exc_info.value.__cause__ is None


def test_restoration_unknown_or_null_original_default_fails_closed(monkeypatch):
    """If original_default is None or 'null', restoration cannot succeed and fails closed."""
    fake = StatefulFakeAdb()
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    ctx = PortalImeContext(TEST_SERIAL)
    ctx.original_default = None
    with pytest.raises(RuntimeError) as exc_info:
        ctx.__exit__(None, None, None)
    assert str(exc_info.value) == "Device IME restoration failed"

    ctx.original_default = "null"
    with pytest.raises(RuntimeError) as exc_info2:
        ctx.__exit__(None, None, None)
    assert str(exc_info2.value) == "Device IME restoration failed"


def test_run_adb_exception_with_private_token_never_leaks(monkeypatch):
    """Subprocess exception carrying sensitive token must not leak into raised error message or chain."""
    private_token = "PRIVATE_SECRET_BEARER_TOKEN_987654321"
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    ctx = PortalImeContext(TEST_SERIAL)
    ctx.__enter__()

    # Inject exception containing private token during exit restoration
    fake.fail_rules["shell ime set " + GBOARD_IME] = RuntimeError(f"ADB process crashed with token={private_token}")

    with pytest.raises(RuntimeError) as exc_info:
        ctx.__exit__(None, None, None)

    err_msg = str(exc_info.value)
    assert err_msg == "Device IME restoration failed"
    assert private_token not in err_msg
    assert exc_info.value.__cause__ is None


def test_pending_successful_return_inside_with_cancelled_by_restoration_failure(monkeypatch):
    """If a successful return is initiated inside with, but __exit__ restoration fails, return is cancelled."""
    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    # In restoration, default readback mismatches
    def bad_exit_readback(cmd: list[str]) -> AdbResult:
        # After enter finishes, return wrong default
        if len(fake.calls) > 6:
            return AdbResult(0, "com.corrupted.ime/.CorruptedIME\n", "")
        return AdbResult(0, f"{GBOARD_IME}\n", "")

    fake.fail_rules["shell settings get secure default_input_method"] = bad_exit_readback

    def execute_with_success():
        with PortalImeContext(TEST_SERIAL):
            return {"passed": True, "token": "proof_valid"}

    with pytest.raises(RuntimeError) as exc_info:
        execute_with_success()

    assert str(exc_info.value) == "Device IME restoration failed"


def test_collector_failure_sanitizer_regression_with_fake(monkeypatch):
    """Regression check: collector catches restoration RuntimeError and converts to failed proof."""
    from phone_runner.identity_proof import collect_own_url_proof_chain

    fake = StatefulFakeAdb(
        default_ime=GBOARD_IME,
        enabled_imes=[GBOARD_IME],
        installed_imes=[GBOARD_IME, PORTAL_IME],
    )
    monkeypatch.setattr("phone_runner.identity_proof.run_adb", fake)

    # Mock probe and pre-checks to pass
    monkeypatch.setattr(
        "phone_runner.identity_proof.probe_device",
        lambda s, wake=False: {
            "adb_connected": True,
            "device_awake": True,
            "facebook_installed": True,
            "portal_installed": True,
            "facebook_version": "1.0",
            "portal_version": "1.0",
            "resolution": "1080x2400",
            "rotation": 0,
        },
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.verify_foreground_facebook",
        lambda s, geom=None: (False, "Simulated abort inside with", None, ""),
    )
    monkeypatch.setattr(
        "phone_runner.identity_proof.get_local_app_card_version",
        lambda: "1.0",
    )

    # Force restoration to fail in exit
    fake.fail_rules["shell ime disable " + PORTAL_IME] = AdbResult(1, "", "Disable failed")

    result = collect_own_url_proof_chain(
        serial=TEST_SERIAL,
        main_name="Test User",
        expected_root_id="10001",
        expected_geometry="1080x2400",
    )

    assert result.passed is False
    assert "unexpected error: RuntimeError" in result.error
    assert result.active_composer_actor_id is None
