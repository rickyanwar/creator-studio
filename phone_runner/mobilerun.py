from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile

from phone_runner.adb import run_adb
from phone_runner.config import (
    get_default_timeout,
    get_mobilerun_gateway_url,
    get_mobilerun_model,
    get_mobilerun_path,
    get_mobilerun_provider,
    get_mobilerun_timeout,
    is_serial_valid_format,
)

PORTAL_IME_COMPONENT = "com.mobilerun.portal/.input.MobilerunKeyboardIME"


def snapshot_device_ime(serial: str) -> tuple[bool, str, bool]:
    """Snapshots original default IME and Portal enabled state before CLI execution."""
    res_default = run_adb(["shell", "settings", "get", "secure", "default_input_method"], serial=serial)
    if res_default.returncode != 0 or not res_default.stdout.strip():
        return False, "", False
    orig_default = res_default.stdout.strip()

    # Validate original default is a real component (not null/missing/unformatted)
    if not orig_default or "/" not in orig_default or "null" in orig_default.lower():
        return False, "", False

    res_enabled = run_adb(["shell", "ime", "list", "-s"], serial=serial)
    if res_enabled.returncode != 0:
        return False, "", False
    enabled_list = [line.strip() for line in res_enabled.stdout.splitlines() if line.strip()]

    portal_was_enabled = PORTAL_IME_COMPONENT in enabled_list

    return True, orig_default, portal_was_enabled


def restore_device_ime(serial: str, orig_default: str, portal_was_enabled: bool) -> tuple[bool, str]:
    """Restores original default IME and Portal enabled state to match snapshot.

    Verifies returncodes, readback match, and exact enabled state.
    """
    if portal_was_enabled:
        res_en = run_adb(["shell", "ime", "enable", PORTAL_IME_COMPONENT], serial=serial)
        if res_en.returncode != 0:
            return False, "Failed to execute IME enable command"

    if orig_default:
        res_set = run_adb(["shell", "ime", "set", orig_default], serial=serial)
        if res_set.returncode != 0:
            return False, "Failed to execute IME set command"

        res_check = run_adb(["shell", "settings", "get", "secure", "default_input_method"], serial=serial)
        if res_check.returncode != 0 or res_check.stdout.strip() != orig_default:
            return False, "Default IME readback mismatch during restoration"

    if not portal_was_enabled:
        res_dis = run_adb(["shell", "ime", "disable", PORTAL_IME_COMPONENT], serial=serial)
        if res_dis.returncode != 0:
            return False, "Failed to execute IME disable command"

    res_list = run_adb(["shell", "ime", "list", "-s"], serial=serial)
    if res_list.returncode != 0:
        return False, "Failed to query enabled IME list during restoration"

    enabled_list = [line.strip() for line in res_list.stdout.splitlines() if line.strip()]
    portal_now_enabled = PORTAL_IME_COMPONENT in enabled_list
    if portal_now_enabled != portal_was_enabled:
        if portal_was_enabled:
            return False, "Portal IME not enabled after enable command"
        return False, "Portal IME still enabled after disable command"

    return True, ""


@dataclass(frozen=True)
class MobilerunResult:
    returncode: int
    stdout: str
    stderr: str


def get_mobilerun_config_path() -> str:
    env_path = os.environ.get("MOBILERUN_CONFIG_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    pkg_root = Path(__file__).parent.resolve()
    app_cards_dir = (pkg_root / "app_cards").resolve()

    content = (
        "# Runtime Mobilerun configuration with App Cards enabled (schema version 7)\n"
        "_version: 7\n"
        "agent:\n"
        "  app_cards:\n"
        "    enabled: true\n"
        "    mode: local\n"
        f"    app_cards_dir: {app_cards_dir}\n"
    )
    runtime_config = pkg_root / "mobilerun_runtime.yaml"
    try:
        if not runtime_config.is_file() or runtime_config.read_text(encoding="utf-8") != content:
            runtime_config.write_text(content, encoding="utf-8")
        return str(runtime_config)
    except OSError:
        temp_dir = Path(tempfile.gettempdir())
        tmp_runtime_config = temp_dir / "mobilerun_runtime.yaml"
        try:
            if not tmp_runtime_config.is_file() or tmp_runtime_config.read_text(encoding="utf-8") != content:
                tmp_runtime_config.write_text(content, encoding="utf-8")
            return str(tmp_runtime_config)
        except OSError:
            return str(runtime_config)


def run_mobilerun(
    serial: str,
    prompt: str,
    steps: int = 5,
    timeout: float | None = None,
    no_reasoning: bool = False,
    save_trajectory: str | None = None,
) -> MobilerunResult:
    if not is_serial_valid_format(serial):
        return MobilerunResult(
            returncode=-1,
            stdout="",
            stderr=f"Invalid serial format: {serial}",
        )

    mobilerun_bin = get_mobilerun_path()
    gateway_url = get_mobilerun_gateway_url()
    model = get_mobilerun_model()
    provider = get_mobilerun_provider()
    config_path = get_mobilerun_config_path()

    cmd: list[str] = [
        mobilerun_bin,
        "run",
        "-d",
        serial,
        "--provider",
        provider,
        "--api_base",
        gateway_url,
        "-m",
        model,
        "--steps",
        str(steps),
        "--vision",
        "-c",
        config_path,
    ]
    if no_reasoning:
        cmd.append("--no-reasoning")
    if save_trajectory is not None:
        cmd.extend(["--save-trajectory", save_trajectory])
    cmd.append(prompt)

    t = timeout if timeout is not None else get_mobilerun_timeout()

    ok, orig_default, portal_was_enabled = snapshot_device_ime(serial)
    if not ok:
        return MobilerunResult(
            returncode=-1,
            stdout="",
            stderr="Failed to query device IME before Mobilerun execution",
        )

    ret_res: MobilerunResult | None = None
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
        )
        ret_res = MobilerunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        ret_res = MobilerunResult(
            returncode=-1,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=f"Mobilerun command timed out after {t}s",
        )
    except FileNotFoundError:
        ret_res = MobilerunResult(
            returncode=-1,
            stdout="",
            stderr=f"Mobilerun executable not found: {mobilerun_bin}",
        )
    except Exception:
        ret_res = MobilerunResult(
            returncode=-1,
            stdout="",
            stderr="Mobilerun execution error",
        )
    finally:
        restore_ok, _ = restore_device_ime(serial, orig_default, portal_was_enabled)
        if not restore_ok:
            ret_res = MobilerunResult(
                returncode=-1,
                stdout=ret_res.stdout if ret_res is not None else "",
                stderr="IME_RESTORATION_FAILED",
            )

    return ret_res


def type_text(
    serial: str,
    text: str,
    clear: bool = True,
    timeout: float | None = None,
) -> MobilerunResult:
    """Deterministically types literal text into active focused input using Mobilerun CLI.

    Executes: mobilerun device type [--clear] -d <serial> <text>
    Uses subprocess argv directly without shell interpolation to prevent command injection.
    """
    if not is_serial_valid_format(serial):
        return MobilerunResult(
            returncode=-1,
            stdout="",
            stderr=f"Invalid serial format: {serial}",
        )

    mobilerun_bin = get_mobilerun_path()
    cmd: list[str] = [
        mobilerun_bin,
        "device",
        "type",
    ]
    if clear:
        cmd.append("--clear")
    cmd.extend(["-d", serial, "--", text])

    t = timeout if timeout is not None else get_default_timeout()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
        )
        return MobilerunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired:
        return MobilerunResult(
            returncode=-1,
            stdout="",
            stderr=f"Mobilerun device type timed out after {t}s",
        )
    except FileNotFoundError:
        return MobilerunResult(
            returncode=-1,
            stdout="",
            stderr=f"Mobilerun executable not found: {mobilerun_bin}",
        )
    except Exception as exc:
        return MobilerunResult(
            returncode=-1,
            stdout="",
            stderr=f"Mobilerun device type error: {type(exc).__name__}",
        )
