"""Fail-closed proxy verification and auto-heal guard for Google Flow."""
import logging
import os
import socket
import subprocess
import time
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def is_proxy_reachable(proxy_url: str, timeout: float = 2.0) -> bool:
    """Check if the proxy host and port can accept a TCP connection."""
    if not proxy_url:
        return True
    try:
        parsed = urlparse(proxy_url)
        host = parsed.hostname
        port = parsed.port or (1080 if "socks" in (parsed.scheme or "") else 8080)
        if not host:
            return False
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (OSError, ValueError):
        return False


def revive_phone_proxy(device: str = "100.64.96.50:5555") -> bool:
    """Attempt to reconnect ADB and start the SOCKS5 proxy service on the phone."""
    try:
        subprocess.run(["adb", "connect", device], capture_output=True, timeout=5)
        time.sleep(1)
        subprocess.run(
            [
                "adb",
                "-s",
                device,
                "shell",
                "am start-foreground-service -a hev.socks5.START hev.socks5/.Socks5Service",
            ],
            capture_output=True,
            timeout=5,
        )
        time.sleep(1)
        return True
    except Exception as exc:
        log.warning("Could not revive proxy via ADB: %s", exc)
        return False


def ensure_proxy_ready(proxy_url: str | None, allow_auto_heal: bool = True) -> bool:
    """Ensure proxy is alive before touching Google Flow.

    Fail-closed: returns True if proxy is ready (or unconfigured).
    If proxy is configured and unreachable, attempts auto-heal once.
    If still unreachable, raises RuntimeError to prevent exposing datacenter IP.
    """
    if not proxy_url:
        return True

    if is_proxy_reachable(proxy_url):
        return True

    if allow_auto_heal:
        log.warning("Configured proxy %s is not reachable; attempting auto-heal...", proxy_url)
        revive_phone_proxy()
        if is_proxy_reachable(proxy_url, timeout=3.0):
            log.info("Proxy %s successfully revived via auto-heal.", proxy_url)
            return True

    msg = (
        f"CRITICAL: Configured proxy ({proxy_url}) is unreachable! "
        f"Refusing to launch Flow to prevent exposing datacenter IP."
    )
    log.error(msg)
    raise RuntimeError(msg)
