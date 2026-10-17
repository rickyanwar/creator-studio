from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import re
import subprocess
import time
from typing import Any
import uuid
import xml.etree.ElementTree as ET

logger = logging.getLogger("phone_runner.recovery")

A11Y_HELPER_PATH = "/usr/local/bin/enable-mobilerun-a11y.sh"
PORTAL_A11Y_COMPONENT = "com.mobilerun.portal/.service.MobilerunAccessibilityService"

VALID_RECOVERY_CLASSES = {"routine", "larger_repair", "larger"}

VALID_REASON_CODES = {
    "routine_check",
    "device_offline",
    "screen_off",
    "asleep",
    "portal_unready",
    "portal_offline",
    "accessibility_disabled",
    "a11y_disabled",
    "app_not_responding",
    "app_crash",
    "system_unresponsive",
    "connectivity_lost",
    "stale_lease",
    "unknown_failure",
}

SERIAL_REGEX = re.compile(r"^[a-zA-Z0-9.:_-]{1,64}$")
REASON_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def get_adb_path() -> str:
    return os.environ.get("ADB_PATH", "adb")


def get_default_timeout() -> float:
    try:
        return float(os.environ.get("DEFAULT_TIMEOUT_SEC", "15.0"))
    except ValueError:
        return 15.0


def get_hermes_path() -> str:
    return os.environ.get("HERMES_PATH", "hermes")


def get_hermes_timeout() -> float:
    # A one-shot Hermes repair took 173 s live (2026-10-17: Portal force-stopped, ColorOS blocks adb
    # secure-settings writes). The backend /recover client waits 330 s.
    try:
        return float(os.environ.get("HERMES_TIMEOUT_SEC", "240.0"))
    except ValueError:
        return 240.0


def get_recovery_settle_seconds() -> float:
    """Pause before the final re-check: Portal can take a few seconds to answer after a revive."""
    try:
        return max(0.0, float(os.environ.get("RECOVERY_SETTLE_SEC", "8.0")))
    except ValueError:
        return 8.0


def hermes_command(args: list[str], timeout: float) -> list[str]:
    """Builds the Hermes argv. The phone-runner unit keeps /root read-only (ProtectHome=read-only), but
    Hermes must write /root/.hermes, so under the unit (HERMES_OUTSIDE_SANDBOX=1) it runs as a transient
    systemd unit outside this sandbox. The runner's own sandbox is unchanged."""
    cmd = [get_hermes_path(), *args]
    if os.environ.get("HERMES_OUTSIDE_SANDBOX") == "1":
        return [
            "systemd-run", "--quiet", "--wait", "--pipe", "--collect",
            f"--property=RuntimeMaxSec={int(timeout)}", *cmd,
        ]
    return cmd


def build_hermes_repair_prompt(serial: str, reason: str) -> str:
    return (
        f"Phone-runner device recovery for Android device {serial} (reason: {reason}). "
        f"Using adb only, make `adb -s {serial} shell content query --uri content://com.mobilerun.portal/ping` "
        "return pong again: re-enable the Mobilerun Portal accessibility service "
        "(com.mobilerun.portal/com.mobilerun.portal.service.MobilerunAccessibilityService) and reconnect adb if needed. "
        "STRICT RULES: do NOT open, tap, type in or change Facebook or any other app; never post; never switch accounts; "
        "never change unrelated settings; never unlock a PIN/password lock. Stop as soon as ping returns pong or after "
        "three attempts. Reply with exactly one line: OK, or FAILED: <short reason>."
    )


def is_serial_valid_format(serial: str) -> bool:
    if not isinstance(serial, str):
        return False
    return bool(SERIAL_REGEX.match(serial))


def get_allowed_serials() -> set[str]:
    raw = os.environ.get("PHONE_RUNNER_ALLOWED_SERIALS", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def is_serial_allowed(serial: str) -> bool:
    if not is_serial_valid_format(serial):
        return False
    return serial in get_allowed_serials()


def is_valid_reason_code(reason: str) -> bool:
    if not isinstance(reason, str) or not REASON_REGEX.match(reason):
        return False
    return reason in VALID_REASON_CODES


def sanitize_error(text: str) -> str:
    if not text:
        return ""
    cleaned = re.sub(r"(Bearer\s+)[A-Za-z0-9_\-\.]+", r"\1[REDACTED]", text)
    cleaned = re.sub(
        r"(token|secret|key|password)[=:]\s*['\"]?[A-Za-z0-9_\-\.]+['\"]?",
        r"\1=[REDACTED]",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"[\r\n]+", " ", cleaned).strip()
    if len(cleaned) > 256:
        cleaned = cleaned[:253] + "..."
    return cleaned


@dataclass(frozen=True)
class AdbResult:
    returncode: int
    stdout: str
    stderr: str


def run_adb(
    args: list[str],
    serial: str | None = None,
    timeout: float | None = None,
    subprocess_runner: Any = None,
) -> AdbResult:
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
    runner = subprocess_runner or subprocess.run
    try:
        proc = runner(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
            shell=False,
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
            stderr=sanitize_error(f"Execution error: {exc}"),
        )


def check_device_state(serial: str, subprocess_runner: Any = None) -> AdbResult:
    return run_adb(["get-state"], serial=serial, subprocess_runner=subprocess_runner)


def query_power_state(serial: str, subprocess_runner: Any = None) -> AdbResult:
    return run_adb(["shell", "dumpsys", "power"], serial=serial, subprocess_runner=subprocess_runner)


def wake_and_unlock(serial: str, subprocess_runner: Any = None) -> AdbResult:
    run_adb(["shell", "input", "keyevent", "224"], serial=serial, subprocess_runner=subprocess_runner)
    return run_adb(["shell", "wm", "dismiss-keyguard"], serial=serial, subprocess_runner=subprocess_runner)


def ping_portal(serial: str, subprocess_runner: Any = None) -> AdbResult:
    return run_adb(
        ["shell", "content", "query", "--uri", "content://com.mobilerun.portal/ping"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )


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


def parse_bounds(bounds_str: str) -> tuple[int, int] | None:
    if not bounds_str:
        return None
    m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", bounds_str)
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return (x1 + x2) // 2, (y1 + y2) // 2


def find_portal_switch(xml_text: str) -> tuple[int, int, bool] | None:
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text.strip())
    except Exception:
        return None

    has_portal_title = False
    for node in root.iter("node"):
        pkg = node.attrib.get("package", "")
        cls = node.attrib.get("class", "")
        text = node.attrib.get("text", "")
        if pkg == "com.android.settings" and "TextView" in cls and text == "Mobilerun Portal":
            has_portal_title = True
            break

    if not has_portal_title:
        return None

    for node in root.iter("node"):
        pkg = node.attrib.get("package", "")
        cls = node.attrib.get("class", "")
        if pkg == "com.android.settings" and cls == "android.widget.Switch":
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            if not bounds:
                continue
            checked = node.attrib.get("checked", "").lower() == "true"
            text = node.attrib.get("text", "").strip()
            is_on = checked or text in ("On", "Aktif")
            return bounds[0], bounds[1], is_on

    return None


def find_dialog_button(
    xml_text: str,
    labels: tuple[str, ...] = ("Allow", "Izinkan", "OK", "Turn on", "Aktifkan"),
) -> tuple[int, int] | None:
    if not xml_text:
        return None
    try:
        root = ET.fromstring(xml_text.strip())
    except Exception:
        return None

    allowed_pkgs = {"com.android.settings", "com.android.permissioncontroller", "android"}
    norm_labels = {lbl.strip().lower() for lbl in labels}
    for node in root.iter("node"):
        pkg = node.attrib.get("package", "")
        clickable = node.attrib.get("clickable", "").lower() == "true"
        text = node.attrib.get("text", "").strip()
        if not clickable or pkg not in allowed_pkgs:
            continue
        text_lower = text.lower()
        if text_lower == "deny":
            continue
        if text_lower in norm_labels:
            bounds = parse_bounds(node.attrib.get("bounds", ""))
            if bounds:
                return bounds

    return None


def dump_ui_hierarchy(serial: str, subprocess_runner: Any = None) -> str | None:
    tmp_path = f"/sdcard/uidump_{uuid.uuid4().hex[:8]}.xml"
    try:
        dump_res = run_adb(
            ["shell", "uiautomator", "dump", tmp_path],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        if dump_res.returncode != 0 or "error" in (dump_res.stdout + dump_res.stderr).lower():
            return None

        cat_res = run_adb(
            ["exec-out", "cat", tmp_path],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        if cat_res.returncode != 0 or not cat_res.stdout.strip():
            cat_res = run_adb(
                ["shell", "cat", tmp_path],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )
        if cat_res.returncode != 0 or not cat_res.stdout.strip():
            return None
        return cat_res.stdout
    finally:
        run_adb(
            ["shell", "rm", "-f", tmp_path],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )


def revive_accessibility_via_settings_ui(
    serial: str,
    subprocess_runner: Any = None,
) -> bool:
    power_res = query_power_state(serial, subprocess_runner=subprocess_runner)
    was_asleep = parse_screen_state(power_res.stdout) == "asleep"

    def _sleep(seconds: float) -> None:
        time.sleep(0.0 if subprocess_runner is not None else seconds)

    try:
        wake_and_unlock(serial, subprocess_runner=subprocess_runner)
        # Focus stays on NotificationShade (or null) for ~1-2 s after dismiss-keyguard,
        # even without a secure lock (seen live 2026-10-16); checking at once would always bail out.
        _sleep(3.0)

        win_res = run_adb(
            ["shell", "dumpsys", "window"],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        focus_lines = [l for l in win_res.stdout.splitlines() if "mCurrentFocus" in l]
        focus_text = " ".join(focus_lines) if focus_lines else win_res.stdout
        if re.search(r"keyguard|notificationshade", focus_text, re.IGNORECASE):
            return False

        run_adb(
            [
                "shell",
                "am",
                "start",
                "-a",
                "android.settings.ACCESSIBILITY_DETAILS_SETTINGS",
                "--es",
                "android.intent.extra.COMPONENT_NAME",
                PORTAL_A11Y_COMPONENT,
            ],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        _sleep(3.0)

        win_res = run_adb(
            ["shell", "dumpsys", "window"],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        focus_lines = [l for l in win_res.stdout.splitlines() if "mCurrentFocus" in l]
        focus_text = " ".join(focus_lines) if focus_lines else win_res.stdout
        if "com.android.settings" not in focus_text:
            run_adb(
                ["shell", "am", "start", "-a", "android.settings.ACCESSIBILITY_SETTINGS"],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )
            _sleep(3.0)
            win_res = run_adb(
                ["shell", "dumpsys", "window"],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )
            focus_lines = [l for l in win_res.stdout.splitlines() if "mCurrentFocus" in l]
            focus_text = " ".join(focus_lines) if focus_lines else win_res.stdout
            if "com.android.settings" not in focus_text:
                return False

        ui_xml = dump_ui_hierarchy(serial, subprocess_runner=subprocess_runner)
        if not ui_xml:
            return False

        switch_info = find_portal_switch(ui_xml)
        if switch_info is None:
            return False

        sx, sy, is_on = switch_info
        if not is_on:
            run_adb(
                ["shell", "input", "tap", str(sx), str(sy)],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )
            _sleep(3.0)

            dialog_xml = dump_ui_hierarchy(serial, subprocess_runner=subprocess_runner)
            if not dialog_xml:
                return False

            btn_coords = find_dialog_button(dialog_xml)
            if not btn_coords:
                return False

            bx, by = btn_coords
            run_adb(
                ["shell", "input", "tap", str(bx), str(by)],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )
            _sleep(2.0)

        chk_after = run_adb(
            ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        return chk_after.returncode == 0 and "com.mobilerun.portal/" in chk_after.stdout
    finally:
        run_adb(
            ["shell", "input", "keyevent", "3"],
            serial=serial,
            subprocess_runner=subprocess_runner,
        )
        if was_asleep:
            run_adb(
                ["shell", "input", "keyevent", "223"],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )


def check_and_revive_accessibility(
    serial: str,
    subprocess_runner: Any = None,
    allow_helper_script: bool = False,
    allow_ui_revive: bool = True,
) -> dict[str, Any]:
    if not is_serial_allowed(serial):
        return {"check": "accessibility", "status": "failed", "detail": "serial not allowed"}

    chk = run_adb(
        ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    if chk.returncode == 0 and "com.mobilerun.portal/" in chk.stdout:
        return {"check": "accessibility", "status": "ok", "detail": "mobilerun accessibility enabled"}

    existing = chk.stdout.strip() if chk.returncode == 0 else ""
    if not existing or existing == "null":
        new_val = PORTAL_A11Y_COMPONENT
    else:
        new_val = f"{existing}:{PORTAL_A11Y_COMPONENT}"

    put1 = run_adb(
        ["shell", "settings", "put", "secure", "enabled_accessibility_services", new_val],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    put2 = run_adb(
        ["shell", "settings", "put", "secure", "accessibility_enabled", "1"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )

    secure_settings_blocked = False
    for res in (put1, put2):
        out_err = f"{res.stdout} {res.stderr}"
        if "SecurityException" in out_err or "WRITE_SECURE_SETTINGS" in out_err:
            secure_settings_blocked = True

    chk_after = run_adb(
        ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    if chk_after.returncode == 0 and "com.mobilerun.portal/" in chk_after.stdout:
        return {"check": "accessibility", "status": "recovered", "detail": "accessibility enabled via adb settings"}

    if allow_ui_revive:
        if revive_accessibility_via_settings_ui(serial, subprocess_runner=subprocess_runner):
            return {"check": "accessibility", "status": "recovered", "detail": "accessibility enabled via Settings UI"}

    if allow_helper_script and os.path.isfile(A11Y_HELPER_PATH) and os.access(A11Y_HELPER_PATH, os.X_OK):
        runner = subprocess_runner or subprocess.run
        try:
            helper_res = runner(
                [A11Y_HELPER_PATH, "-s", serial],
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
                shell=False,
            )
            if helper_res.returncode == 0:
                return {"check": "accessibility", "status": "recovered", "detail": "revived via a11y helper"}
        except Exception as exc:
            logger.warning("Accessibility helper script execution failed: %s", exc)

    if secure_settings_blocked:
        return {
            "check": "accessibility",
            "status": "failed",
            "detail": (
                "secure settings write blocked by the OS (ColorOS permission monitoring); "
                "enable it on the phone: Settings > Accessibility > Mobilerun Portal > On, "
                "or Developer options > Disable permission monitoring"
            ),
            "error_code": "secure_settings_blocked",
        }

    return {"check": "accessibility", "status": "failed", "detail": "accessibility service inactive"}


def run_deterministic_recovery(
    serial: str,
    subprocess_runner: Any = None,
    allow_helper_script: bool = False,
    restore_sleep: bool = True,
    allow_ui_revive: bool = True,
) -> tuple[bool, list[dict[str, Any]], str | None]:
    checks: list[dict[str, Any]] = []

    state_res = check_device_state(serial, subprocess_runner=subprocess_runner)
    is_connected = state_res.returncode == 0 and "device" in state_res.stdout.strip()
    if not is_connected:
        checks.append({
            "check": "connectivity",
            "status": "failed",
            "detail": "device unreachable via adb",
        })
        return False, checks, "device unreachable via adb"

    checks.append({
        "check": "connectivity",
        "status": "ok",
        "detail": "adb connected",
    })

    power_res = query_power_state(serial, subprocess_runner=subprocess_runner)
    screen = parse_screen_state(power_res.stdout)
    was_asleep = screen == "asleep"
    woke_screen = False

    try:
        if was_asleep:
            wake_and_unlock(serial, subprocess_runner=subprocess_runner)
            power_res_after = query_power_state(serial, subprocess_runner=subprocess_runner)
            screen_after = parse_screen_state(power_res_after.stdout)
            if screen_after != "asleep":
                woke_screen = True
                checks.append({
                    "check": "display_state",
                    "status": "recovered",
                    "screen": screen_after,
                    "detail": "device successfully woken and unlocked",
                })
            else:
                checks.append({
                    "check": "display_state",
                    "status": "failed",
                    "screen": "asleep",
                    "detail": "device remained asleep after wake attempt",
                })
                return False, checks, "screen wake failed"
        else:
            checks.append({
                "check": "display_state",
                "status": "ok",
                "screen": screen,
                "detail": "device display active",
            })

        ping_res = ping_portal(serial, subprocess_runner=subprocess_runner)
        portal_ready = ping_res.returncode == 0 and "pong" in ping_res.stdout.lower()
        if not portal_ready:
            run_adb(
                ["shell", "am", "start-foreground-service", "-n", "com.mobilerun.portal/.PortalService"],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )
            ping_after = ping_portal(serial, subprocess_runner=subprocess_runner)
            portal_ready = ping_after.returncode == 0 and "pong" in ping_after.stdout.lower()
            if portal_ready:
                checks.append({
                    "check": "portal_health",
                    "status": "recovered",
                    "detail": "portal service restarted and responding",
                })
            else:
                checks.append({
                    "check": "portal_health",
                    "status": "failed",
                    "detail": "portal ping failed",
                })
                a11y_res = check_and_revive_accessibility(
                    serial,
                    subprocess_runner=subprocess_runner,
                    allow_helper_script=allow_helper_script,
                    allow_ui_revive=allow_ui_revive,
                )
                checks.append(a11y_res)
                return False, checks, "portal health check failed"
        else:
            checks.append({
                "check": "portal_health",
                "status": "ok",
                "detail": "portal pong received",
            })

        a11y_res = check_and_revive_accessibility(
            serial,
            subprocess_runner=subprocess_runner,
            allow_helper_script=allow_helper_script,
            allow_ui_revive=allow_ui_revive,
        )
        checks.append(a11y_res)
        if a11y_res["status"] not in ("ok", "recovered"):
            return False, checks, "accessibility unready"

        return True, checks, None
    finally:
        if was_asleep and woke_screen and restore_sleep:
            run_adb(
                ["shell", "input", "keyevent", "223"],
                serial=serial,
                subprocess_runner=subprocess_runner,
            )


def invoke_hermes_repair(
    serial: str,
    reason: str,
    subprocess_runner: Any = None,
    timeout: float | None = None,
) -> tuple[bool, str]:
    hermes_bin = get_hermes_path()
    t = timeout if timeout is not None else get_hermes_timeout()
    # There is no `hermes heal` command: use a one-shot agent run with a strict, device-only prompt.
    cmd = hermes_command(["-z", build_hermes_repair_prompt(serial, reason)], t)
    runner = subprocess_runner or subprocess.run
    try:
        proc = runner(
            cmd,
            capture_output=True,
            text=True,
            timeout=t,
            check=False,
            shell=False,
        )
        if proc.returncode == 0:
            return True, "hermes healing succeeded"
        err = sanitize_error(proc.stderr or proc.stdout or f"exit code {proc.returncode}")
        return False, f"hermes healing failed: {err}"
    except subprocess.TimeoutExpired:
        return False, f"hermes healing timed out after {t}s"
    except FileNotFoundError:
        return False, f"hermes executable not found: {hermes_bin}"
    except Exception as exc:
        return False, f"hermes execution error: {sanitize_error(str(exc))}"


def send_owner_notification(
    message: str,
    subprocess_runner: Any = None,
) -> tuple[bool, str]:
    hermes_bin = get_hermes_path()
    clean_msg = sanitize_error(message)
    # Real CLI syntax: `hermes send -t <target> <message>` (there are no --to/--message flags).
    cmd = hermes_command(["send", "-t", "telegram", clean_msg], 15.0)
    runner = subprocess_runner or subprocess.run
    try:
        proc = runner(
            cmd,
            capture_output=True,
            text=True,
            timeout=15.0,
            check=False,
            shell=False,
        )
        if proc.returncode == 0:
            return True, "sent"
        err = sanitize_error(proc.stderr or proc.stdout or f"exit code {proc.returncode}")
        return False, f"failed: {err}"
    except subprocess.TimeoutExpired:
        return False, "timed out"
    except FileNotFoundError:
        return False, f"executable not found: {hermes_bin}"
    except Exception as exc:
        return False, sanitize_error(str(exc))


def execute_recovery(
    serial: str,
    recovery_class: str = "routine",
    reason: str = "routine_check",
    owner_decision: str | None = None,
    auto_approve_after: str | None = None,
    owner_notified_at: str | None = None,
    now_utc: datetime | None = None,
    subprocess_runner: Any = None,
    allow_helper_script: bool = False,
    restore_sleep: bool = True,
) -> dict[str, Any]:
    now = now_utc or datetime.now(timezone.utc)

    if not is_serial_valid_format(serial):
        return {
            "status": "failed",
            "state": "needs_human",
            "next_state": "needs_human",
            "error": "invalid_serial_format",
            "serial": serial,
            "recovery_class": recovery_class,
            "reason_code": reason,
            "check_count": 0,
            "hermes_attempt_count": 0,
            "checks": [],
            "owner_notified_at": owner_notified_at,
            "auto_approve_after": auto_approve_after,
            "owner_decision": owner_decision,
            "notification_sent": False,
            "timestamp": now.isoformat(),
        }

    if not is_serial_allowed(serial):
        return {
            "status": "failed",
            "state": "needs_human",
            "next_state": "needs_human",
            "error": "serial_not_allowed",
            "serial": serial,
            "recovery_class": recovery_class,
            "reason_code": reason,
            "check_count": 0,
            "hermes_attempt_count": 0,
            "checks": [],
            "owner_notified_at": owner_notified_at,
            "auto_approve_after": auto_approve_after,
            "owner_decision": owner_decision,
            "notification_sent": False,
            "timestamp": now.isoformat(),
        }

    if recovery_class not in VALID_RECOVERY_CLASSES:
        return {
            "status": "failed",
            "state": "needs_human",
            "next_state": "needs_human",
            "error": "invalid_recovery_class",
            "serial": serial,
            "recovery_class": recovery_class,
            "reason_code": reason,
            "check_count": 0,
            "hermes_attempt_count": 0,
            "checks": [],
            "owner_notified_at": owner_notified_at,
            "auto_approve_after": auto_approve_after,
            "owner_decision": owner_decision,
            "notification_sent": False,
            "timestamp": now.isoformat(),
        }

    if not is_valid_reason_code(reason):
        return {
            "status": "failed",
            "state": "needs_human",
            "next_state": "needs_human",
            "error": "invalid_reason",
            "serial": serial,
            "recovery_class": recovery_class,
            "reason_code": reason,
            "check_count": 0,
            "hermes_attempt_count": 0,
            "checks": [],
            "owner_notified_at": owner_notified_at,
            "auto_approve_after": auto_approve_after,
            "owner_decision": owner_decision,
            "notification_sent": False,
            "timestamp": now.isoformat(),
        }

    check_count = 1
    hermes_attempt_count = 0

    det_ok, checks, det_err = run_deterministic_recovery(
        serial,
        subprocess_runner=subprocess_runner,
        allow_helper_script=allow_helper_script,
        restore_sleep=restore_sleep,
    )
    if det_ok:
        return {
            "status": "resolved",
            "state": "resolved",
            "next_state": "ready",
            "serial": serial,
            "recovery_class": recovery_class,
            "reason_code": reason,
            "check_count": check_count,
            "hermes_attempt_count": 0,
            "checks": checks,
            "owner_notified_at": owner_notified_at,
            "auto_approve_after": auto_approve_after,
            "owner_decision": owner_decision,
            "notification_sent": False,
            "error": None,
            "timestamp": now.isoformat(),
        }

    if recovery_class in ("larger_repair", "larger"):
        if owner_decision in ("rejected", "denied", "cancelled"):
            return {
                "status": "needs_human",
                "state": "needs_human",
                "next_state": "needs_human",
                "serial": serial,
                "recovery_class": recovery_class,
                "reason_code": reason,
                "check_count": check_count,
                "hermes_attempt_count": 0,
                "checks": checks,
                "owner_notified_at": owner_notified_at,
                "auto_approve_after": auto_approve_after,
                "owner_decision": owner_decision,
                "notification_sent": False,
                "error": "larger_repair_rejected_by_owner",
                "timestamp": now.isoformat(),
            }

        if owner_decision != "approved":
            if not auto_approve_after:
                deadline = now + timedelta(minutes=15)
                notif_msg = f"[REPAIR NOTICE] Device {serial} requires larger repair ({reason}). 15-minute approval window open until {deadline.isoformat()}."
                notif_ok, notif_detail = send_owner_notification(notif_msg, subprocess_runner=subprocess_runner)
                return {
                    "status": "awaiting_approval",
                    "state": "awaiting_approval",
                    "next_state": "awaiting_approval",
                    "serial": serial,
                    "recovery_class": recovery_class,
                    "reason_code": reason,
                    "check_count": check_count,
                    "hermes_attempt_count": 0,
                    "checks": checks,
                    "owner_notified_at": now.isoformat(),
                    "auto_approve_after": deadline.isoformat(),
                    "owner_decision": None,
                    "notification_sent": notif_ok,
                    "error": det_err,
                    "timestamp": now.isoformat(),
                }

            try:
                deadline_dt = datetime.fromisoformat(auto_approve_after.replace("Z", "+00:00"))
                if deadline_dt.tzinfo is None:
                    deadline_dt = deadline_dt.replace(tzinfo=timezone.utc)
            except Exception:
                deadline_dt = now + timedelta(minutes=15)

            if now < deadline_dt:
                return {
                    "status": "awaiting_approval",
                    "state": "awaiting_approval",
                    "next_state": "awaiting_approval",
                    "serial": serial,
                    "recovery_class": recovery_class,
                    "reason_code": reason,
                    "check_count": check_count,
                    "hermes_attempt_count": 0,
                    "checks": checks,
                    "owner_notified_at": owner_notified_at,
                    "auto_approve_after": auto_approve_after,
                    "owner_decision": None,
                    "notification_sent": False,
                    "error": det_err,
                    "timestamp": now.isoformat(),
                }

    hermes_attempt_count += 1
    hermes_ok, hermes_msg = invoke_hermes_repair(
        serial,
        reason,
        subprocess_runner=subprocess_runner,
    )
    if hermes_ok:
        recheck_ok, rechecks, recheck_err = run_deterministic_recovery(
            serial,
            subprocess_runner=subprocess_runner,
            allow_helper_script=allow_helper_script,
            restore_sleep=restore_sleep,
        )
        checks.extend(rechecks)
        if recheck_ok:
            return {
                "status": "resolved",
                "state": "resolved",
                "next_state": "ready",
                "serial": serial,
                "recovery_class": recovery_class,
                "reason_code": reason,
                "check_count": check_count + 1,
                "hermes_attempt_count": hermes_attempt_count,
                "checks": checks,
                "owner_notified_at": owner_notified_at,
                "auto_approve_after": auto_approve_after,
                "owner_decision": owner_decision,
                "notification_sent": False,
                "error": None,
                "timestamp": now.isoformat(),
            }

    # Final re-check before paging the owner: Portal often answers a few seconds after a revive,
    # which used to produce a false "needs_human" for a device that was already healthy.
    time.sleep(get_recovery_settle_seconds())
    late_ok, late_checks, _late_err = run_deterministic_recovery(
        serial,
        subprocess_runner=subprocess_runner,
        allow_helper_script=allow_helper_script,
        restore_sleep=restore_sleep,
    )
    checks.extend(late_checks)
    if late_ok:
        return {
            "status": "resolved",
            "state": "resolved",
            "next_state": "ready",
            "serial": serial,
            "recovery_class": recovery_class,
            "reason_code": reason,
            "check_count": check_count + 1,
            "hermes_attempt_count": hermes_attempt_count,
            "checks": checks,
            "owner_notified_at": owner_notified_at,
            "auto_approve_after": auto_approve_after,
            "owner_decision": owner_decision,
            "notification_sent": False,
            "error": None,
            "timestamp": now.isoformat(),
        }

    notif_msg = f"[ALERT] Device {serial} recovery exhausted ({reason}). Manual intervention required."
    notif_ok, notif_detail = send_owner_notification(notif_msg, subprocess_runner=subprocess_runner)

    return {
        "status": "needs_human",
        "state": "needs_human",
        "next_state": "needs_human",
        "serial": serial,
        "recovery_class": recovery_class,
        "reason_code": reason,
        "check_count": check_count,
        "hermes_attempt_count": hermes_attempt_count,
        "checks": checks,
        "owner_notified_at": owner_notified_at or now.isoformat(),
        "auto_approve_after": auto_approve_after,
        "owner_decision": owner_decision,
        "notification_sent": notif_ok,
        "error": sanitize_error(hermes_msg or det_err or "recovery_exhausted"),
        "timestamp": now.isoformat(),
    }
