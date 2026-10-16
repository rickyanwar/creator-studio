from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import sys
import threading
from typing import Any

from phone_runner.config import (
    get_bind_host,
    get_bind_port,
    is_serial_allowed,
    is_serial_valid_format,
    verify_token,
)
from phone_runner.discovery import DiscoveryError, discover_devices
from phone_runner.dry_run import execute_dry_run
from phone_runner.probe import probe_device
from phone_runner.publish import execute_publish

logger = logging.getLogger("phone_runner")
MAX_REQUEST_SIZE = 64 * 1024  # 64 KB

_serial_locks: dict[str, threading.Lock] = {}
_serial_locks_mutex = threading.Lock()


def get_serial_lock(serial: str) -> threading.Lock:
    with _serial_locks_mutex:
        if serial not in _serial_locks:
            _serial_locks[serial] = threading.Lock()
        return _serial_locks[serial]


class PhoneRunnerRequestHandler(BaseHTTPRequestHandler):
    server_version = "PhoneRunner/0.1"

    def log_message(self, format: str, *args: Any) -> None:
        # Standardized log without dumping raw payloads or secrets
        logger.info("%s - - [%s] %s", self.address_string(), self.log_date_time_string(), format % args)

    def send_json(self, status_code: int, data: dict[str, Any]) -> None:
        try:
            body = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def check_auth(self) -> bool:
        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = self.headers.get("X-Phone-Runner-Token", "").strip()

        return verify_token(token)

    def read_json_body(self) -> dict[str, Any] | None:
        content_length_header = self.headers.get("Content-Length")
        if not content_length_header:
            self.send_json(400, {"error": "missing_content_length"})
            return None

        try:
            length = int(content_length_header)
        except ValueError:
            self.send_json(400, {"error": "invalid_content_length"})
            return None

        if length < 0:
            self.send_json(400, {"error": "invalid_content_length"})
            return None

        if length > MAX_REQUEST_SIZE:
            self.send_json(413, {"error": "payload_too_large"})
            return None

        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self.send_json(400, {"error": "malformed_json"})
            return None

        if not isinstance(data, dict):
            self.send_json(400, {"error": "invalid_json_object"})
            return None

        return data

    def do_GET(self) -> None:
        if self.path == "/health":
            self.send_json(200, {"status": "ok", "service": "phone-runner"})
            return

        if self.path == "/devices/discovery":
            if not self.check_auth():
                self.send_json(401, {"error": "unauthorized"})
                return
            try:
                data = discover_devices()
                self.send_json(200, data)
            except (DiscoveryError, Exception):
                self.send_json(503, {"error": "discovery_failed", "detail": "Tailscale discovery unavailable"})
            return

        self.send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path not in ("/probe", "/dry-run", "/publish", "/recover", "/notify"):
            self.send_json(404, {"error": "not_found"})
            return

        if not self.check_auth():
            self.send_json(401, {"error": "unauthorized"})
            return

        body = self.read_json_body()
        if body is None:
            return

        if self.path == "/notify":
            message = body.get("message")
            if not message or not isinstance(message, str):
                self.send_json(400, {"error": "missing_or_invalid_message"})
                return

            channel = body.get("channel", "telegram")
            if channel != "telegram":
                self.send_json(400, {"error": "unsupported_channel"})
                return

            from phone_runner.recovery import send_owner_notification

            sent_ok, detail = send_owner_notification(message)
            self.send_json(200, {"sent": sent_ok, "detail": detail})
            return

        serial = body.get("serial")
        if not serial:
            self.send_json(400, {"error": "missing_serial"})
            return

        if not is_serial_valid_format(serial):
            self.send_json(400, {"error": "invalid_serial_format"})
            return

        if not is_serial_allowed(serial):
            self.send_json(403, {"error": "serial_not_allowed"})
            return

        if self.path == "/probe":
            wake = bool(body.get("wake", False))
            res = probe_device(serial, wake=wake)
            self.send_json(200, res)
            return

        # Mutating device operations (/dry-run, /publish, /recover) acquire exclusive non-blocking per-serial lock
        serial_lock = get_serial_lock(serial)
        if not serial_lock.acquire(blocking=False):
            self.send_json(409, {"error": "device_busy", "detail": f"Device {serial} is busy running another operation"})
            return

        try:
            if self.path == "/dry-run":
                main_profile = body.get("main_profile")
                if not main_profile:
                    self.send_json(400, {"error": "missing_main_profile"})
                    return

                target_page = body.get("target_page")
                expected_geometry = body.get("expected_geometry")
                app_card_version = body.get("app_card_version")

                res = execute_dry_run(
                    serial=serial,
                    main_profile=main_profile,
                    target_page=target_page,
                    expected_geometry=expected_geometry,
                    app_card_version=app_card_version,
                )
                self.send_json(200, res)
                return

            if self.path == "/publish":
                main_profile = body.get("main_profile")
                if not main_profile or not isinstance(main_profile, (dict, str)):
                    self.send_json(400, {"error": "missing_main_profile"})
                    return

                destination = body.get("destination")
                if not destination or not isinstance(destination, dict):
                    self.send_json(400, {"error": "missing_destination"})
                    return

                caption = body.get("caption", "")
                media_urls = body.get("media_urls")
                if media_urls is not None and not isinstance(media_urls, list):
                    self.send_json(400, {"error": "invalid_media_urls"})
                    return

                media_kind = body.get("media_kind")
                expected_geometry = body.get("expected_geometry")
                app_card_version = body.get("app_card_version")

                res = execute_publish(
                    serial=serial,
                    main_profile=main_profile,
                    destination=destination,
                    caption=caption,
                    media_urls=media_urls,
                    media_kind=media_kind,
                    expected_geometry=expected_geometry,
                    app_card_version=app_card_version,
                )
                self.send_json(200, res)
                return

            if self.path == "/recover":
                forbidden = {"cmd", "command", "exec", "shell", "script", "code", "run", "raw_command"}
                if any(k in body for k in forbidden):
                    self.send_json(400, {"error": "arbitrary_commands_not_allowed"})
                    return

                recovery_class = body.get("recovery_class", "routine")
                if not isinstance(recovery_class, str) or recovery_class not in ("routine", "larger_repair", "larger"):
                    self.send_json(400, {"error": "invalid_recovery_class"})
                    return

                reason = body.get("reason", "routine_check")
                if not isinstance(reason, str):
                    self.send_json(400, {"error": "invalid_reason"})
                    return

                from phone_runner.recovery import execute_recovery, is_valid_reason_code

                if not is_valid_reason_code(reason):
                    self.send_json(400, {"error": "invalid_reason"})
                    return

                target = body.get("target") or body.get("target_serial") or body.get("device_serial")
                if target and target != serial:
                    self.send_json(400, {"error": "ambiguous_target_selection"})
                    return

                owner_decision = body.get("owner_decision")
                if owner_decision is not None and not isinstance(owner_decision, str):
                    self.send_json(400, {"error": "invalid_owner_decision"})
                    return

                auto_approve_after = body.get("auto_approve_after")
                owner_notified_at = body.get("owner_notified_at")

                res = execute_recovery(
                    serial=serial,
                    recovery_class=recovery_class,
                    reason=reason,
                    owner_decision=owner_decision,
                    auto_approve_after=auto_approve_after,
                    owner_notified_at=owner_notified_at,
                )
                self.send_json(200, res)
                return
        finally:
            serial_lock.release()


def create_server(host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    h = host if host is not None else get_bind_host()
    p = port if port is not None else get_bind_port()
    return ThreadingHTTPServer((h, p), PhoneRunnerRequestHandler)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    host = get_bind_host()
    port = get_bind_port()
    logger.info("Starting phone-runner host service on %s:%d", host, port)
    server = create_server(host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping phone-runner host service")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
