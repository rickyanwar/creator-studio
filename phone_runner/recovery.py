from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
import re
import subprocess
from typing import Any

logger = logging.getLogger("phone_runner.recovery")

A11Y_HELPER_PATH = "/usr/local/bin/enable-mobilerun-a11y.sh"

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
    try:
        return float(os.environ.get("HERMES_TIMEOUT_SEC", "30.0"))
    except ValueError:
        return 30.0


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


def check_and_revive_accessibility(
    serial: str,
    subprocess_runner: Any = None,
    allow_helper_script: bool = False,
) -> dict[str, Any]:
    if not is_serial_allowed(serial):
        return {"check": "accessibility", "status": "failed", "detail": "serial not allowed"}

    chk = run_adb(
        ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    if chk.returncode == 0 and "com.mobilerun.portal" in chk.stdout:
        return {"check": "accessibility", "status": "ok", "detail": "mobilerun accessibility enabled"}

    run_adb(
        [
            "shell",
            "settings",
            "put",
            "secure",
            "enabled_accessibility_services",
            "com.mobilerun.portal/.service.AccessibilityService:com.mobilerun.portal/.AccessibilityService",
        ],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    run_adb(
        ["shell", "settings", "put", "secure", "accessibility_enabled", "1"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    chk_after = run_adb(
        ["shell", "settings", "get", "secure", "enabled_accessibility_services"],
        serial=serial,
        subprocess_runner=subprocess_runner,
    )
    if chk_after.returncode == 0 and "com.mobilerun.portal" in chk_after.stdout:
        return {"check": "accessibility", "status": "recovered", "detail": "accessibility enabled via adb settings"}

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

    return {"check": "accessibility", "status": "unready", "detail": "accessibility service inactive"}


def run_deterministic_recovery(
    serial: str,
    subprocess_runner: Any = None,
    allow_helper_script: bool = False,
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
    if screen == "asleep":
        wake_and_unlock(serial, subprocess_runner=subprocess_runner)
        power_res_after = query_power_state(serial, subprocess_runner=subprocess_runner)
        screen_after = parse_screen_state(power_res_after.stdout)
        if screen_after != "asleep":
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
    )
    checks.append(a11y_res)
    if a11y_res["status"] not in ("ok", "recovered"):
        return False, checks, "accessibility unready"

    return True, checks, None


def invoke_hermes_repair(
    serial: str,
    reason: str,
    subprocess_runner: Any = None,
    timeout: float | None = None,
) -> tuple[bool, str]:
    hermes_bin = get_hermes_path()
    t = timeout if timeout is not None else get_hermes_timeout()
    cmd = [hermes_bin, "heal", "--serial", serial, "--reason", reason]
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
    cmd = [hermes_bin, "send", "--to", "telegram", "--message", clean_msg]
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
