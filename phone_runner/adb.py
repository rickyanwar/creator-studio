import base64
from dataclasses import dataclass
import json
import re
import subprocess
import uuid
import xml.etree.ElementTree as ET

from phone_runner.config import (
    get_adb_path,
    get_default_timeout,
    is_serial_valid_format,
)
from phone_runner.resolver import is_allowed_facebook_url


@dataclass(frozen=True)
class AdbResult:
    returncode: int
    stdout: str
    stderr: str


def run_adb(args: list[str], serial: str | None = None, timeout: float | None = None) -> AdbResult:
    if serial is not None and not is_serial_valid_format(serial):
        return AdbResult(
            returncode=-1,
            stdout="",
            stderr=f"Invalid serial format: {serial}",
        )

    adb_bin = get_adb_path()
    cmd: list[str] = [adb_bin]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)

    t = timeout if timeout is not None else get_default_timeout()
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
        )
        return AdbResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
        )
    except subprocess.TimeoutExpired as exc:
        return AdbResult(
            returncode=-1,
            stdout=(exc.stdout or "") if isinstance(exc.stdout, str) else "",
            stderr=f"Command timed out after {t}s",
        )
    except FileNotFoundError:
        return AdbResult(
            returncode=-1,
            stdout="",
            stderr=f"Executable not found: {adb_bin}",
        )
    except Exception as exc:
        return AdbResult(
            returncode=-1,
            stdout="",
            stderr=f"Execution error: {exc}",
        )


def check_device_state(serial: str) -> AdbResult:
    return run_adb(["get-state"], serial=serial)


def query_power_state(serial: str) -> AdbResult:
    return run_adb(["shell", "dumpsys", "power"], serial=serial)


def query_screen_size(serial: str) -> AdbResult:
    return run_adb(["shell", "wm", "size"], serial=serial)


def query_window_state(serial: str) -> AdbResult:
    return run_adb(["shell", "dumpsys", "window"], serial=serial)


def query_package_info(serial: str, package_name: str) -> AdbResult:
    return run_adb(["shell", "dumpsys", "package", package_name], serial=serial)


def ping_portal(serial: str) -> AdbResult:
    return run_adb(
        ["shell", "content", "query", "--uri", "content://com.mobilerun.portal/ping"],
        serial=serial,
    )


def wake_and_unlock(serial: str) -> AdbResult:
    # 224 = KEYCODE_WAKEUP
    run_adb(["shell", "input", "keyevent", "224"], serial=serial)
    return run_adb(["shell", "wm", "dismiss-keyguard"], serial=serial)


def put_to_sleep(serial: str) -> AdbResult:
    # 223 = KEYCODE_SLEEP (idempotent; avoids waking display if already asleep)
    return run_adb(["shell", "input", "keyevent", "223"], serial=serial)


def is_device_secure_locked(serial: str) -> bool:
    """Checks if the device is locked with a secure keyguard (PIN/Pattern/Password).

    Fails closed (returns True) on error, missing/empty output, ambiguous, or
    conflicting state.
    """
    win_res = run_adb(["shell", "dumpsys", "window", "policy"], serial=serial)
    if win_res.returncode != 0 or not win_res.stdout or not win_res.stdout.strip():
        return True

    text = win_res.stdout

    # 1. Look for KeyguardServiceDelegate section
    section_lines = []
    in_delegate = False
    delegate_indent = -1
    for line in text.splitlines():
        if not in_delegate:
            m = re.search(r"^(\s*)(?:KeyguardServiceDelegate|mKeyguardDelegate)\b", line, re.IGNORECASE)
            if m:
                in_delegate = True
                delegate_indent = len(m.group(1))
        else:
            stripped = line.strip()
            if not stripped:
                continue
            cur_indent = len(line) - len(line.lstrip())
            if cur_indent > delegate_indent:
                section_lines.append(stripped)
            else:
                break

    if in_delegate and section_lines:
        section = "\n".join(section_lines)
        showings = set(re.findall(r"\bshowing=(true|false)\b", section, re.IGNORECASE))
        secures = set(re.findall(r"\bsecure=(true|false)\b", section, re.IGNORECASE))

        # Conflicting values within keyguard section fail closed
        if len(showings) > 1 or len(secures) > 1:
            return True

        if showings and secures:
            is_showing = (list(showings)[0].lower() == "true")
            is_secure = (list(secures)[0].lower() == "true")
            if not is_showing:
                return False  # Positively observed unlocked
            if not is_secure:
                return False  # Positively observed nonsecure keyguard (swipe lock)
            return True       # Positively observed secure lock

    # 2. Retain supported legacy forms if KeyguardServiceDelegate is absent
    out_lower = text.lower()
    legacy_showings = set(re.findall(r"\b(?:mshowinglockscreen|mkeyguardshowing)=(true|false)\b", out_lower))
    legacy_secures = set(re.findall(r"\b(?:iskeyguardsecure|mkeyguardsecure)=(true|false)\b", out_lower))

    if len(legacy_showings) > 1 or len(legacy_secures) > 1:
        return True

    if legacy_showings and legacy_secures:
        showing_val = list(legacy_showings)[0] == "true"
        secure_val = list(legacy_secures)[0] == "true"
        if not showing_val:
            return False
        if not secure_val:
            return False
        return True

    # Unknown or unobserved keyguard state fails closed
    return True


def launch_facebook_feed(serial: str) -> AdbResult:
    return run_adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "fb://feed",
            "com.facebook.katana",
        ],
        serial=serial,
    )


def launch_facebook_own_profile(serial: str) -> AdbResult:
    return run_adb(
        [
            "shell",
            "am",
            "start",
            "-a",
            "android.intent.action.VIEW",
            "-d",
            "fb://profile",
            "com.facebook.katana",
        ],
        serial=serial,
    )


def query_ui_hierarchy(serial: str) -> AdbResult:
    """Dumps and reads the active UI hierarchy XML safely.

    Uses a fresh per-invocation temp path, checks for dump failure or ERROR output,
    validates XML parseability, cleans up the temp file in finally, and returns
    a nonzero result with empty stdout on any capture failure.
    """
    tmp_path = f"/data/local/tmp/uidump_{uuid.uuid4().hex}.xml"
    try:
        dump_res = run_adb(["shell", "uiautomator", "dump", tmp_path], serial=serial)
        dump_err_text = f"{dump_res.stdout}\n{dump_res.stderr}".lower()
        if dump_res.returncode != 0 or "error" in dump_err_text:
            return AdbResult(
                returncode=-1 if dump_res.returncode == 0 else dump_res.returncode,
                stdout="",
                stderr="UI hierarchy dump failed: uiautomator error",
            )

        cat_res = run_adb(["shell", "cat", tmp_path], serial=serial)
        if cat_res.returncode != 0:
            return AdbResult(
                returncode=cat_res.returncode if cat_res.returncode != 0 else -1,
                stdout="",
                stderr="UI hierarchy read failed: cat error",
            )

        content = cat_res.stdout
        if not content or not content.strip():
            return AdbResult(
                returncode=-1,
                stdout="",
                stderr="UI hierarchy capture empty",
            )

        try:
            root = ET.fromstring(content.strip())
            if root.tag != "hierarchy":
                return AdbResult(
                    returncode=-1,
                    stdout="",
                    stderr="UI hierarchy root element invalid",
                )
        except Exception:
            return AdbResult(
                returncode=-1,
                stdout="",
                stderr="UI hierarchy XML malformed",
            )

        return AdbResult(
            returncode=0,
            stdout=content,
            stderr="",
        )
    finally:
        rm_res = run_adb(["shell", "rm", "-f", tmp_path], serial=serial)
        if rm_res.returncode != 0:
            return AdbResult(
                returncode=-1 if rm_res.returncode == 0 else rm_res.returncode,
                stdout="",
                stderr="UI hierarchy cleanup failed",
            )


def query_activity_state(serial: str) -> AdbResult:
    return run_adb(["shell", "dumpsys", "activity", "top"], serial=serial)


def parse_screen_state(dumpsys_power_output: str) -> str:
    if not dumpsys_power_output:
        return "unknown"

    m_wake = re.search(r"mWakefulness=(\w+)", dumpsys_power_output, re.IGNORECASE)
    if m_wake:
        val = m_wake.group(1).lower()
        if "awake" in val:
            return "awake"
        if "asleep" in val or "dozing" in val or "dreaming" in val:
            return "asleep"

    m_display = re.search(r"Display Power:\s*state=(\w+)", dumpsys_power_output, re.IGNORECASE)
    if m_display:
        val = m_display.group(1).upper()
        if val == "ON":
            return "awake"
        if val == "OFF":
            return "asleep"

    m_is_awake = re.search(r"isAwake=(\w+)", dumpsys_power_output, re.IGNORECASE)
    if m_is_awake:
        return "awake" if m_is_awake.group(1).lower() == "true" else "asleep"

    return "unknown"


def parse_resolution(wm_size_output: str) -> str | None:
    if not wm_size_output:
        return None
    # Prioritize Override size if present, otherwise Physical size
    m_override = re.search(r"Override\s+size:\s*(\d+x\d+)", wm_size_output)
    if m_override:
        return m_override.group(1)
    m_phys = re.search(r"Physical\s+size:\s*(\d+x\d+)", wm_size_output)
    if m_phys:
        return m_phys.group(1)
    return None


def parse_rotation(dumpsys_window_output: str) -> int:
    if not dumpsys_window_output:
        return 0
    patterns = [
        r"mCurrentRotation=ROTATION_(\d+)",
        r"mOrientation=(\d+)",
        r"surface orientation:\s*(\d+)",
        r"mRotation=(\d+)",
        r"rotation:\s*(\d+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, dumpsys_window_output, re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return 0


def parse_package_version(dumpsys_package_output: str) -> str | None:
    if not dumpsys_package_output:
        return None
    m = re.search(r"versionName=([^\s\r\n]+)", dumpsys_package_output)
    return m.group(1) if m else None


def clear_device_clipboard(serial: str, sentinel: str | None = None) -> AdbResult:
    """Clears device clipboard using a fresh random sentinel and Portal /clipboard/set.

    Uses safe argv without shell execution. On success, returns AdbResult(returncode=0, stdout=sentinel).
    On failure or error, returns AdbResult(returncode=-1, stdout="", stderr=sanitized_error).
    """
    if sentinel is None:
        sentinel = f"sentinel_{uuid.uuid4().hex}"

    b64_sentinel = base64.b64encode(sentinel.encode("utf-8")).decode("ascii")
    cmd = [
        "shell",
        "content",
        "insert",
        "--uri",
        "content://com.mobilerun.portal/clipboard/set",
        "--bind",
        f"text_base64:s:{b64_sentinel}",
    ]
    res = run_adb(cmd, serial=serial)
    if res.returncode != 0:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard clear failed")

    out_lower = f"{res.stdout}\n{res.stderr}".lower()
    if "error" in out_lower or "keyboard" in out_lower:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard clear failed: Portal error")

    return AdbResult(returncode=0, stdout=sentinel, stderr="")


def set_device_clipboard_text(serial: str, text: str) -> AdbResult:
    """Puts text on the device clipboard via Portal /clipboard/set (base64, so newlines/emoji/accents survive)."""
    b64_text = base64.b64encode((text or "").encode("utf-8")).decode("ascii")
    cmd = [
        "shell",
        "content",
        "insert",
        "--uri",
        "content://com.mobilerun.portal/clipboard/set",
        "--bind",
        f"text_base64:s:{b64_text}",
    ]
    res = run_adb(cmd, serial=serial)
    if res.returncode != 0:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard set failed")
    out_lower = f"{res.stdout}\n{res.stderr}".lower()
    if "error" in out_lower or "keyboard" in out_lower:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard set failed: Portal error")
    return AdbResult(returncode=0, stdout="", stderr="")


def read_device_clipboard(serial: str, expected_sentinel: str | None = None) -> AdbResult:
    """Reads device clipboard via Mobilerun Portal content://com.mobilerun.portal/clipboard/get.

    Validates JSON envelope strictly. Only returns the sentinel or an allowlisted Facebook URL.
    All unknown/unauthorized clipboard content is withheld (empty stdout, sanitized error).
    """
    cmd = [
        "shell",
        "content",
        "query",
        "--uri",
        "content://com.mobilerun.portal/clipboard/get",
    ]
    res = run_adb(cmd, serial=serial)
    if res.returncode != 0:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard query failed")

    out_text = res.stdout or ""
    out_lower = f"{out_text}\n{res.stderr}".lower()
    if "keyboard" in out_lower or "ime" in out_lower:
        return AdbResult(returncode=-1, stdout="", stderr="Portal keyboard not active")

    max_envelope_size = 8192
    if len(out_text) > max_envelope_size:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard envelope oversized")

    start_idx = out_text.find("{")
    end_idx = out_text.rfind("}")
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard envelope malformed")

    raw_json = out_text[start_idx : end_idx + 1]
    try:
        data = json.loads(raw_json)
    except Exception:
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard envelope malformed")

    if not isinstance(data, dict):
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard envelope malformed")

    if data.get("status") != "success":
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard read not successful")

    if "result" not in data or not isinstance(data["result"], str):
        return AdbResult(returncode=-1, stdout="", stderr="Clipboard payload invalid")

    payload = data["result"]

    # Allowed outputs: expected sentinel, sentinel format, or allowlisted Facebook URL
    is_sentinel = False
    if expected_sentinel is not None and payload == expected_sentinel:
        is_sentinel = True
    elif re.fullmatch(r"sentinel_[0-9a-fA-F]{16,64}", payload):
        is_sentinel = True

    if is_sentinel or is_allowed_facebook_url(payload):
        return AdbResult(returncode=0, stdout=payload, stderr="")

    # Unknown content must be withheld, never returned or logged
    return AdbResult(returncode=-1, stdout="", stderr="Clipboard content withheld")
