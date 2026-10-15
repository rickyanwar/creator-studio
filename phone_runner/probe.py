from datetime import datetime, timezone
from typing import Any

from phone_runner.adb import (
    check_device_state,
    parse_package_version,
    parse_resolution,
    parse_rotation,
    parse_screen_state,
    ping_portal,
    query_package_info,
    query_power_state,
    query_screen_size,
    query_window_state,
    wake_and_unlock,
)


def probe_device(serial: str, wake: bool = False) -> dict[str, Any]:
    checked_at = datetime.now(timezone.utc).isoformat()

    state_res = check_device_state(serial)
    is_connected = state_res.returncode == 0 and "device" in state_res.stdout.strip()
    if not is_connected:
        return {
            "status": "remote_unreachable",
            "adb_connected": False,
            "portal_ready": False,
            "screen": "unknown",
            "resolution": None,
            "rotation": None,
            "facebook_version": None,
            "portal_version": None,
            "checked_at": checked_at,
        }

    power_res = query_power_state(serial)
    screen = parse_screen_state(power_res.stdout)

    if wake and screen == "asleep":
        wake_and_unlock(serial)
        power_res_after = query_power_state(serial)
        screen_after = parse_screen_state(power_res_after.stdout)
        screen = screen_after if screen_after != "unknown" else "awake"

    size_res = query_screen_size(serial)
    resolution = parse_resolution(size_res.stdout)

    win_res = query_window_state(serial)
    rotation = parse_rotation(win_res.stdout)

    fb_res = query_package_info(serial, "com.facebook.katana")
    facebook_version = parse_package_version(fb_res.stdout)

    portal_pkg_res = query_package_info(serial, "com.mobilerun.portal")
    portal_version = parse_package_version(portal_pkg_res.stdout)

    ping_res = ping_portal(serial)
    portal_ready = ping_res.returncode == 0 and "pong" in ping_res.stdout.lower()

    if screen == "asleep":
        status = "asleep_reachable"
    elif not portal_ready:
        status = "portal_unready"
    else:
        status = "ready"

    return {
        "status": status,
        "adb_connected": True,
        "portal_ready": portal_ready,
        "screen": screen,
        "resolution": resolution,
        "rotation": rotation,
        "facebook_version": facebook_version,
        "portal_version": portal_version,
        "checked_at": checked_at,
    }
