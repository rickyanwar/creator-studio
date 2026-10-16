"""Tailscale and ADB device discovery module for Phone Runner."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import subprocess
from typing import Any

from phone_runner.adb import run_adb
from phone_runner.config import is_serial_allowed

TAILSCALE_CGNAT_NET = ipaddress.ip_network("100.64.0.0/10")
MAX_TAILSCALE_OUTPUT_BYTES = 10 * 1024 * 1024
ALLOWED_ADB_STATES = {"device", "offline", "unauthorized", "not_connected", "unavailable"}
SAFE_IDENTIFIER_REGEX = re.compile(r"^[A-Za-z0-9_.:-]+$")
CONTROL_CHARS_REGEX = re.compile(r"[\r\n\x00-\x1f\x7f-\x9f]")


class DiscoveryError(Exception):
    """Raised when Tailscale device discovery cannot complete safely."""


def _get_tailscale_path() -> str:
    return os.environ.get("TAILSCALE_PATH", "tailscale")


def _extract_connection_info(data: dict[str, Any]) -> dict[str, Any]:
    backend_state = str(data.get("BackendState") or "").strip()[:64] or "Running"

    current_tailnet = data.get("CurrentTailnet")
    tailnet_name = None
    if isinstance(current_tailnet, dict):
        raw_tailnet = current_tailnet.get("Name")
        if isinstance(raw_tailnet, str) and raw_tailnet.strip():
            tailnet_name = CONTROL_CHARS_REGEX.sub(" ", raw_tailnet).strip()[:128] or None

    self_info = data.get("Self")
    user_id = None
    if isinstance(self_info, dict):
        user_id = self_info.get("UserID")

    account_name = None
    if user_id is not None:
        user_map = data.get("User")
        if isinstance(user_map, dict):
            user_data = user_map.get(str(user_id))
            if isinstance(user_data, dict):
                raw_login = user_data.get("LoginName")
                if isinstance(raw_login, str) and raw_login.strip():
                    account_name = CONTROL_CHARS_REGEX.sub(" ", raw_login).strip()[:128] or None

    return {
        "backend_state": backend_state,
        "tailnet_name": tailnet_name,
        "account_name": account_name,
    }


def _run_tailscale_status(timeout: float = 10.0) -> tuple[dict[str, Any], dict[str, Any]]:
    cmd = [_get_tailscale_path(), "status", "--json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        raise DiscoveryError("Tailscale status unavailable") from exc

    if proc.returncode != 0:
        raise DiscoveryError("Tailscale status unavailable")

    raw_stdout = proc.stdout or ""
    if len(raw_stdout.encode("utf-8")) > MAX_TAILSCALE_OUTPUT_BYTES:
        raise DiscoveryError("Tailscale status unavailable")

    try:
        data = json.loads(raw_stdout)
    except Exception as exc:
        raise DiscoveryError("Tailscale status unavailable") from exc

    if not isinstance(data, dict):
        raise DiscoveryError("Tailscale status unavailable")

    backend_state = data.get("BackendState")
    if backend_state != "Running":
        raise DiscoveryError("Tailscale status unavailable")

    peer_map = data.get("Peer")
    if peer_map is None or not isinstance(peer_map, dict):
        raise DiscoveryError("Tailscale status unavailable")

    connection_info = _extract_connection_info(data)
    return peer_map, connection_info


def _parse_adb_devices() -> tuple[dict[str, str], list[str]]:
    warnings: list[str] = []
    adb_res = run_adb(["devices", "-l"], timeout=5.0)
    if adb_res.returncode != 0:
        warnings.append("adb_unavailable")
        return {}, warnings

    devices: dict[str, str] = {}
    for line in adb_res.stdout.splitlines():
        line = line.strip()
        if not line or line.startswith("List of devices") or line.startswith("*"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            serial = parts[0]
            state = parts[1]
            devices[serial] = state

    return devices, warnings


def _match_adb_device(
    ip: str,
    adb_devices: dict[str, str],
    adb_unavailable: bool,
) -> tuple[str, str]:
    if adb_unavailable:
        return f"{ip}:5555", "unavailable"

    matches: list[tuple[str, str]] = []
    for serial, state in adb_devices.items():
        if ":" in serial:
            host, _, port = serial.partition(":")
            if host == ip and port.isdigit() and (1 <= int(port) <= 65535):
                matches.append((serial, state))

    if not matches:
        return f"{ip}:5555", "not_connected"

    # Prefer connected serial ("device") then deterministic serial string
    connected = [m for m in matches if m[1] == "device"]
    if connected:
        connected.sort(key=lambda m: m[0])
        chosen_serial, chosen_state = connected[0]
    else:
        matches.sort(key=lambda m: m[0])
        chosen_serial, chosen_state = matches[0]

    if chosen_state not in ALLOWED_ADB_STATES:
        chosen_state = "unavailable"

    return chosen_serial, chosen_state


def discover_devices(tailscale_timeout: float = 10.0) -> dict[str, Any]:
    """Discovers Android phones from Tailscale and correlates with ADB state."""
    peer_map, connection_info = _run_tailscale_status(timeout=tailscale_timeout)
    adb_devices, warnings = _parse_adb_devices()
    adb_unavailable = "adb_unavailable" in warnings

    seen_ips: set[str] = set()
    seen_node_ids: set[str] = set()
    discovered: list[dict[str, Any]] = []

    for peer in peer_map.values():
        if not isinstance(peer, dict):
            continue

        os_name = str(peer.get("OS") or "").strip().lower()
        if os_name != "android":
            continue

        raw_ips = peer.get("TailscaleIPs")
        if not isinstance(raw_ips, list):
            continue

        valid_ip: str | None = None
        for ip_str in raw_ips:
            if not isinstance(ip_str, str):
                continue
            try:
                ip_obj = ipaddress.ip_address(ip_str.strip())
                if ip_obj.version == 4 and ip_obj in TAILSCALE_CGNAT_NET:
                    valid_ip = str(ip_obj)
                    break
            except ValueError:
                continue

        if not valid_ip or valid_ip in seen_ips:
            continue

        raw_node_id = peer.get("ID")
        node_id: str | None = None
        if raw_node_id is not None and isinstance(raw_node_id, (str, int)):
            s_id = str(raw_node_id).strip()[:128]
            if s_id and SAFE_IDENTIFIER_REGEX.match(s_id):
                node_id = s_id

        if node_id and node_id in seen_node_ids:
            continue

        seen_ips.add(valid_ip)
        if node_id:
            seen_node_ids.add(node_id)

        hostname = peer.get("HostName")
        if not hostname or not isinstance(hostname, str):
            hostname = peer.get("DisplayName") or valid_ip
        clean_name = CONTROL_CHARS_REGEX.sub(" ", str(hostname)).strip()[:128] or valid_ip

        online = peer.get("Online") is True

        adb_serial, adb_state = _match_adb_device(valid_ip, adb_devices, adb_unavailable)
        runner_allowed = is_serial_allowed(adb_serial)

        discovered.append({
            "name": clean_name,
            "tailscale_ip": valid_ip,
            "tailscale_node_id": node_id,
            "online": online,
            "adb_serial": adb_serial,
            "adb_state": adb_state,
            "runner_allowed": runner_allowed,
        })

    # Deterministic ordering: online first, then name, then tailscale_ip
    discovered.sort(key=lambda d: (not d["online"], d["name"], d["tailscale_ip"]))
    discovered = discovered[:200]

    return {
        "devices": discovered,
        "warnings": warnings,
        "connection": connection_info,
    }
