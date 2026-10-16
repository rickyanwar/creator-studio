"""Phone runner host client module for Docker workers (P1).

Communicates with the host-side phone-runner HTTP service over loopback/Docker host gateway.
Guarantees fail-closed semantics, no automatic retries, bounded timeouts, and strict redacting.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import logging
import os
import re
from typing import Any

import httpx

from app.models.social_publishing import (
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.schemas.social_media import PhoneDeviceDiscoveryResponse

logger = logging.getLogger(__name__)

SERIAL_REGEX = re.compile(r"^[a-zA-Z0-9.:_-]{1,64}$")
REASON_REGEX = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
VALID_RECOVERY_CLASSES = {"routine", "larger_repair", "larger"}


def sanitize_error(text: str, max_length: int = 256) -> str:
    """Strip bearer tokens, secrets, passwords and bound string length."""
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
    if len(cleaned) > max_length:
        cleaned = cleaned[: max_length - 3] + "..." if max_length >= 3 else cleaned[:max_length]
    return cleaned


@dataclass
class PhonePublishResult:
    """Structured publish outcome returned to social_delivery_dispatch."""

    status: str  # "published", "waiting_for_device", "uncertain", "failed"
    facebook_post_id: str | None = None
    permalink: str | None = None
    actor_id: str | None = None
    publish_may_have_started: bool = False
    error_code: str | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    stages: list[dict] = field(default_factory=list)


@dataclass
class PhoneRecoveryResult:
    """Structured recovery outcome returned from phone-runner host service."""

    status: str  # "resolved", "awaiting_approval", "needs_human", "failed"
    state: str  # "resolved", "awaiting_approval", "needs_human", "checking", "repairing"
    next_state: str  # "ready", "awaiting_approval", "needs_human"
    serial: str | None = None
    recovery_class: str = "routine"
    reason_code: str = "routine_check"
    check_count: int = 0
    hermes_attempt_count: int = 0
    checks: list[dict[str, Any]] = field(default_factory=list)
    owner_notified_at: str | None = None
    auto_approve_after: str | None = None
    owner_decision: str | None = None
    notification_sent: bool = False
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PhoneRunnerDiscoveryError(Exception):
    """Raised when phone runner discovery cannot complete safely."""

    def __init__(
        self,
        message: str = "Unable to load phones from the server. You can enter connection details manually.",
    ):
        super().__init__(message)
        self.message = message


class PhoneRunnerClient:
    """Client for authenticated communication with phone-runner host service."""

    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        timeout: float = 120.0,
        connect_timeout: float = 10.0,
        dry_run_timeout: float = 260.0,
        publish_timeout: float = 280.0,
    ) -> None:
        self.base_url = (
            base_url if base_url is not None else os.environ.get("PHONE_RUNNER_BASE_URL", "http://127.0.0.1:8765")
        ).strip().rstrip("/")
        self.token = (
            token if token is not None else os.environ.get("PHONE_RUNNER_TOKEN", "")
        ).strip()
        self.timeout = timeout
        self.connect_timeout = connect_timeout
        self.dry_run_timeout = dry_run_timeout
        self.publish_timeout = publish_timeout

    def publish_delivery(
        self,
        *,
        device: PhoneDevice,
        delivery: PublishDelivery,
        main_profile: SocialDestination,
        destination: SocialDestination,
    ) -> PhonePublishResult:
        """Dispatches an immutable delivery payload to the host runner.

        Maps connect failure before request bytes to 'waiting_for_device'.
        Maps read/write timeout or connection loss after connect to 'uncertain'.
        Never retries automatically.
        Never logs tokens, captions, or media content.

        Note: publish_timeout governs client HTTP transport socket timeout (280s),
        not a hard host-side execution deadline. Disconnections or read timeouts
        remain mapped to 'uncertain' with no automatic retry.
        """
        dest_id = str(destination.facebook_actor_id or "").strip()
        main_id = str(main_profile.facebook_actor_id or "").strip()
        main_record_id = str(main_profile.id) if getattr(main_profile, "id", None) is not None else None
        dest_record_id = str(destination.id) if getattr(destination, "id", None) is not None else None
        parent_profile_id = (
            str(destination.parent_profile_id)
            if getattr(destination, "parent_profile_id", None) is not None
            else None
        )

        media_list: list[str] = []
        if delivery.media_snapshot:
            for item in delivery.media_snapshot:
                if isinstance(item, str):
                    media_list.append(item)
                elif isinstance(item, dict) and item.get("url"):
                    media_list.append(str(item["url"]))

        geom = None
        if device.screen_width and device.screen_height:
            geom = {"width": device.screen_width, "height": device.screen_height}

        payload: dict[str, Any] = {
            "serial": device.adb_serial,
            "main_profile": {
                "id": main_id,
                "record_id": main_record_id,
                "name": main_profile.name,
            },
            "destination": {
                "id": dest_id,
                "record_id": dest_record_id,
                "name": destination.name,
                "kind": destination.kind,
                "parent_profile_id": parent_profile_id,
            },
            "caption": delivery.caption_snapshot or "",
            "media_urls": media_list,
            "media_kind": delivery.media_kind,
            "expected_geometry": geom,
            "app_card_version": device.app_card_version,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }

        url = f"{self.base_url}/publish"
        logger.info(
            "PhoneRunnerClient: publishing delivery %s for dest %s on device %s",
            getattr(delivery, "id", None),
            getattr(destination, "id", None),
            getattr(device, "adb_serial", None),
        )

        # ponytail: publish_timeout is HTTP transport budget (240s collector + 40s prep/cleanup < 300s dispatch lease); disconnect remains uncertain/no retry
        timeout_cfg = httpx.Timeout(
            timeout=self.publish_timeout,
            connect=self.connect_timeout,
        )

        try:
            with httpx.Client(timeout=timeout_cfg) as client:
                resp = client.post(url, json=payload, headers=headers)

            if resp.status_code >= 500:
                logger.warning(
                    "PhoneRunnerClient: host service returned 5xx (%s) for delivery %s",
                    resp.status_code,
                    getattr(delivery, "id", None),
                )
                return PhonePublishResult(
                    status="uncertain",
                    actor_id=dest_id,
                    publish_may_have_started=True,
                    error_code=f"http_{resp.status_code}",
                    evidence={"http_status": resp.status_code},
                )

            if resp.status_code != 200:
                logger.warning(
                    "PhoneRunnerClient: host service returned error (%s) for delivery %s",
                    resp.status_code,
                    getattr(delivery, "id", None),
                )
                return PhonePublishResult(
                    status="failed",
                    actor_id=dest_id,
                    publish_may_have_started=False,
                    error_code=f"http_{resp.status_code}",
                    evidence={"http_status": resp.status_code},
                )

            data = resp.json()
            raw_stages = data.get("stages")
            stages: list[dict[str, Any]] = []
            if isinstance(raw_stages, list):
                for item in raw_stages:
                    if not isinstance(item, dict):
                        continue
                    stage_name = item.get("stage")
                    passed = item.get("passed")
                    if not isinstance(stage_name, str) or not stage_name.strip():
                        continue
                    if not isinstance(passed, bool):
                        continue
                    detail = item.get("detail")
                    detail_str = str(detail) if detail is not None else ""
                    stages.append({
                        "stage": stage_name.strip(),
                        "passed": passed,
                        "detail": sanitize_error(detail_str)[:200],
                    })
                    if len(stages) >= 20:
                        break

            return PhonePublishResult(
                status=data.get("status", "failed"),
                facebook_post_id=data.get("facebook_post_id"),
                permalink=data.get("permalink"),
                actor_id=data.get("actor_id"),
                publish_may_have_started=bool(data.get("publish_may_have_started", False)),
                error_code=data.get("error_code"),
                evidence=data.get("evidence") or {},
                stages=stages,
            )

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Failed strictly before any request bytes reached the host runner
            logger.warning(
                "PhoneRunnerClient: pre-request connection failure to host runner: %s",
                type(exc).__name__,
            )
            return PhonePublishResult(
                status="waiting_for_device",
                actor_id=dest_id,
                publish_may_have_started=False,
                error_code="connect_failed",
                evidence={"error_type": type(exc).__name__},
            )

        except (httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout, httpx.RemoteProtocolError, httpx.ReadError) as exc:
            # Timeout or disconnect occurred after request was submitted to host
            logger.error(
                "PhoneRunnerClient: timeout or disconnect during publish execution: %s",
                type(exc).__name__,
            )
            return PhonePublishResult(
                status="uncertain",
                actor_id=dest_id,
                publish_may_have_started=True,
                error_code="timeout_uncertain",
                evidence={"error_type": type(exc).__name__},
            )

        except Exception as exc:
            logger.error("PhoneRunnerClient: unexpected client error: %s", type(exc).__name__)
            return PhonePublishResult(
                status="failed",
                actor_id=dest_id,
                publish_may_have_started=False,
                error_code=f"client_error_{type(exc).__name__}",
                evidence={"error_type": type(exc).__name__},
            )

    def recover_device(
        self,
        *,
        device: PhoneDevice,
        delivery: PublishDelivery | None = None,
        recovery_class: str = "routine",
        reason: str = "routine_check",
        owner_decision: str | None = None,
        auto_approve_after: str | datetime | None = None,
        owner_notified_at: str | datetime | None = None,
    ) -> PhoneRecoveryResult:
        """Calls the host /recover endpoint for an assigned registered device.

        Enforces registered device binding, rejects arbitrary host commands,
        and sanitizes failure details.
        """
        serial = getattr(device, "adb_serial", None)
        if not isinstance(serial, str) or not serial.strip() or not SERIAL_REGEX.match(serial.strip()):
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error="invalid_serial",
                serial=serial if isinstance(serial, str) else None,
                recovery_class=recovery_class,
                reason_code=reason,
            )
        clean_serial = serial.strip()

        if recovery_class not in VALID_RECOVERY_CLASSES:
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error="invalid_recovery_class",
                serial=clean_serial,
                recovery_class=recovery_class,
                reason_code=reason,
            )

        if not isinstance(reason, str) or not REASON_REGEX.match(reason):
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error="invalid_reason",
                serial=clean_serial,
                recovery_class=recovery_class,
                reason_code=reason,
            )

        if owner_decision is not None and not isinstance(owner_decision, str):
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error="invalid_owner_decision",
                serial=clean_serial,
                recovery_class=recovery_class,
                reason_code=reason,
            )

        auto_approve_str = (
            auto_approve_after.isoformat()
            if isinstance(auto_approve_after, datetime)
            else auto_approve_after
        )
        owner_notified_str = (
            owner_notified_at.isoformat()
            if isinstance(owner_notified_at, datetime)
            else owner_notified_at
        )

        payload: dict[str, Any] = {
            "serial": clean_serial,
            "recovery_class": recovery_class,
            "reason": reason,
        }
        if owner_decision:
            payload["owner_decision"] = owner_decision
        if auto_approve_str:
            payload["auto_approve_after"] = auto_approve_str
        if owner_notified_str:
            payload["owner_notified_at"] = owner_notified_str

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        url = f"{self.base_url}/recover"

        logger.info(
            "PhoneRunnerClient: recovering device %s (class=%s, reason=%s)",
            clean_serial,
            recovery_class,
            reason,
        )

        timeout_cfg = httpx.Timeout(
            timeout=self.timeout,
            connect=self.connect_timeout,
        )

        try:
            with httpx.Client(timeout=timeout_cfg) as client:
                resp = client.post(url, json=payload, headers=headers)

            if resp.status_code >= 500:
                logger.warning(
                    "PhoneRunnerClient: host /recover returned 5xx (%s) for serial %s",
                    resp.status_code,
                    clean_serial,
                )
                return PhoneRecoveryResult(
                    status="failed",
                    state="needs_human",
                    next_state="needs_human",
                    error=f"http_{resp.status_code}",
                    serial=clean_serial,
                    recovery_class=recovery_class,
                    reason_code=reason,
                )

            if resp.status_code != 200:
                err_msg = ""
                try:
                    err_msg = resp.json().get("error") or ""
                except Exception:
                    pass
                return PhoneRecoveryResult(
                    status="failed",
                    state="needs_human",
                    next_state="needs_human",
                    error=sanitize_error(err_msg or f"http_{resp.status_code}"),
                    serial=clean_serial,
                    recovery_class=recovery_class,
                    reason_code=reason,
                )

            data = resp.json()
            return PhoneRecoveryResult(
                status=data.get("status", "failed"),
                state=data.get("state", "needs_human"),
                next_state=data.get("next_state", "needs_human"),
                serial=data.get("serial") or clean_serial,
                recovery_class=data.get("recovery_class", recovery_class),
                reason_code=data.get("reason_code", reason),
                check_count=int(data.get("check_count", 0)),
                hermes_attempt_count=int(data.get("hermes_attempt_count", 0)),
                checks=data.get("checks") or [],
                owner_notified_at=data.get("owner_notified_at"),
                auto_approve_after=data.get("auto_approve_after"),
                owner_decision=data.get("owner_decision"),
                notification_sent=bool(data.get("notification_sent", False)),
                error=sanitize_error(data.get("error") or ""),
                raw=data,
            )

        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            logger.warning("PhoneRunnerClient: connect error on /recover: %s", type(exc).__name__)
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error="connect_failed",
                serial=clean_serial,
                recovery_class=recovery_class,
                reason_code=reason,
            )

        except (httpx.TimeoutException, httpx.RequestError) as exc:
            logger.error("PhoneRunnerClient: timeout on /recover: %s", type(exc).__name__)
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error="timeout",
                serial=clean_serial,
                recovery_class=recovery_class,
                reason_code=reason,
            )

        except Exception as exc:
            logger.error("PhoneRunnerClient: unexpected error on /recover: %s", type(exc).__name__)
            return PhoneRecoveryResult(
                status="failed",
                state="needs_human",
                next_state="needs_human",
                error=sanitize_error(f"client_error_{type(exc).__name__}"),
                serial=clean_serial,
                recovery_class=recovery_class,
                reason_code=reason,
            )

    def dry_run(
        self,
        *,
        device: PhoneDevice,
        main_profile: dict[str, Any] | str,
        target_page: dict[str, Any] | str | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Calls host runner POST /dry-run to verify calibration and actor identity."""
        if not self.base_url or not self.token:
            return {
                "success": False,
                "status": "missing_configuration",
                "evidence": {},
                "detail": "Missing base_url or token",
            }

        serial = getattr(device, "adb_serial", None)
        if not serial:
            return {"success": False, "status": "missing_serial", "evidence": {}}

        geom = None
        if device.screen_width and device.screen_height:
            geom = {"width": device.screen_width, "height": device.screen_height}

        payload: dict[str, Any] = {
            "serial": serial,
            "main_profile": main_profile,
            "target_page": target_page,
            "expected_geometry": geom,
            "app_card_version": device.app_card_version,
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        url = f"{self.base_url}/dry-run"

        # ponytail: dry-run bounded to 260s (240s runner bound + 20s transport margin), upgrade if runner adds streaming progress
        effective_timeout = timeout if timeout is not None else self.dry_run_timeout
        timeout_cfg = httpx.Timeout(timeout=effective_timeout, connect=self.connect_timeout)
        try:
            with httpx.Client(timeout=timeout_cfg) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "status": f"http_{resp.status_code}",
                    "evidence": {},
                    "detail": f"host_runner_http_{resp.status_code}",
                }
            try:
                data = resp.json()
                if not isinstance(data, dict):
                    return {
                        "success": False,
                        "status": "malformed_response",
                        "evidence": {},
                        "detail": "Host runner response is not a valid JSON object",
                    }
                raw_success = data.get("success")
                if type(raw_success) is not bool:
                    return {
                        "success": False,
                        "status": "malformed_response",
                        "evidence": {},
                        "detail": "Host runner response success flag must be a boolean",
                    }
                data["success"] = raw_success
                return data
            except Exception:
                return {
                    "success": False,
                    "status": "malformed_response",
                    "evidence": {},
                    "detail": "Failed to decode host runner JSON response",
                }
        except (httpx.ConnectError, httpx.ConnectTimeout):
            return {
                "success": False,
                "status": "connect_timeout",
                "evidence": {},
                "detail": "connect_timeout",
            }
        except (httpx.TimeoutException, httpx.ReadTimeout):
            return {
                "success": False,
                "status": "read_timeout",
                "evidence": {},
                "detail": "read_timeout",
            }
        except Exception as exc:
            return {
                "success": False,
                "status": f"client_error_{type(exc).__name__}",
                "evidence": {},
                "detail": sanitize_error(type(exc).__name__),
            }

    def notify_owner(self, message: str) -> tuple[bool, str]:
        """Routes owner notification to host runner /notify endpoint via Telegram."""
        return self.send_notification(message=message, channel="telegram")

    def send_notification(self, message: str, channel: str = "telegram") -> tuple[bool, str]:
        """Routes owner notification to host runner /notify endpoint."""
        if not self.base_url or not self.token:
            return False, "missing_configuration"

        payload = {"message": message, "channel": channel}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        url = f"{self.base_url}/notify"
        timeout_cfg = httpx.Timeout(timeout=15.0, connect=self.connect_timeout)
        try:
            with httpx.Client(timeout=timeout_cfg) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                try:
                    data = resp.json()
                except Exception:
                    return False, "malformed_response"
                if not isinstance(data, dict):
                    return False, "malformed_response"
                raw_sent = data.get("sent")
                if type(raw_sent) is not bool:
                    return False, "malformed_response"
                if raw_sent is not True:
                    return False, sanitize_error(str(data.get("detail") or ""))
                return True, sanitize_error(str(data.get("detail") or ""))
            return False, f"http_{resp.status_code}"
        except Exception as exc:
            return False, sanitize_error(f"notification_error_{type(exc).__name__}")

    def discover_devices(
        self,
        *,
        timeout: float = 20.0,
        connect_timeout: float = 5.0,
    ) -> dict[str, Any]:
        """Discovers Android phone devices from the host runner via GET /devices/discovery."""
        if not self.base_url or not self.token:
            raise PhoneRunnerDiscoveryError("missing_configuration")

        headers = {
            "Authorization": f"Bearer {self.token}",
        }
        url = f"{self.base_url}/devices/discovery"
        timeout_cfg = httpx.Timeout(timeout=timeout, connect=connect_timeout)

        try:
            with httpx.Client(timeout=timeout_cfg) as client:
                resp = client.get(url, headers=headers)
            if resp.status_code != 200:
                raise PhoneRunnerDiscoveryError(f"host_runner_http_{resp.status_code}")
            try:
                data = resp.json()
            except Exception as exc:
                raise PhoneRunnerDiscoveryError("malformed_json_response") from exc

            if not isinstance(data, dict):
                raise PhoneRunnerDiscoveryError("invalid_json_object")

            try:
                validated = PhoneDeviceDiscoveryResponse.model_validate(data)
                return validated.model_dump()
            except Exception as exc:
                raise PhoneRunnerDiscoveryError("invalid_response_schema") from exc

        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.TimeoutException, httpx.ReadTimeout) as exc:
            raise PhoneRunnerDiscoveryError("timeout") from exc
        except PhoneRunnerDiscoveryError:
            raise
        except Exception as exc:
            raise PhoneRunnerDiscoveryError(f"client_error_{type(exc).__name__}") from exc

    def probe_device(
        self,
        *,
        device: PhoneDevice,
        wake: bool = False,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Probes device status and Mobilerun Portal readiness via POST /probe.

        Never raises an exception.
        """
        if not self.base_url or not self.token:
            return {"success": False, "status": "missing_configuration"}

        serial = getattr(device, "adb_serial", None)
        if not isinstance(serial, str) or not serial.strip() or not SERIAL_REGEX.match(serial.strip()):
            return {
                "success": False,
                "status": "runner_unreachable",
                "detail": "invalid_serial",
            }
        clean_serial = serial.strip()

        payload = {"serial": clean_serial, "wake": bool(wake)}
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        url = f"{self.base_url}/probe"
        effective_timeout = timeout if timeout is not None else 15.0
        timeout_cfg = httpx.Timeout(timeout=effective_timeout, connect=self.connect_timeout)

        try:
            with httpx.Client(timeout=timeout_cfg) as client:
                resp = client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                return {
                    "success": False,
                    "status": "runner_unreachable",
                    "detail": sanitize_error(f"http_{resp.status_code}"),
                }
            try:
                data = resp.json()
            except Exception as exc:
                return {
                    "success": False,
                    "status": "runner_unreachable",
                    "detail": sanitize_error(f"malformed_json_{type(exc).__name__}"),
                }
            if not isinstance(data, dict):
                return {
                    "success": False,
                    "status": "runner_unreachable",
                    "detail": "invalid_json_object",
                }
            data.setdefault("success", True)
            return data
        except Exception as exc:
            return {
                "success": False,
                "status": "runner_unreachable",
                "detail": sanitize_error(type(exc).__name__),
            }
