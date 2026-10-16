"""Periodic watchdog task for phone device health and portal readiness (P2).

Probes active phone publishing devices, handles automated routine recovery,
maintains maintenance status and incident markers, and alerts the owner.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.settings import Settings
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    SocialDestination,
)
from app.services.encryption import decrypt
from app.services.phone_runner_client import PhoneRunnerClient, sanitize_error
from app.services.social_delivery_dispatch import notify_owner_via_hermes
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def send_telegram_fallback(message: str, db: Session) -> bool:
    """Direct fallback to Telegram Bot API when Hermes notification fails.

    Never logs tokens or URLs containing credentials.
    """
    try:
        settings_row = db.query(Settings).first()
        if not settings_row:
            return False
        chat_id = (settings_row.telegram_chat_id or "").strip()
        raw_token_encrypted = settings_row.telegram_bot_token_encrypted
        if not chat_id or not raw_token_encrypted:
            return False
        bot_token = decrypt(raw_token_encrypted).strip()
        if not bot_token:
            return False

        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message}
        resp = httpx.post(url, json=payload, timeout=10.0)
        return resp.status_code == 200
    except Exception as exc:
        logger.warning("Watchdog direct Telegram fallback failed: %s", type(exc).__name__)
        return False


def _check_single_device(db: Session, client: PhoneRunnerClient, device: PhoneDevice) -> None:
    """Evaluates connectivity, Mobilerun Portal health, and recovery for a single phone device."""
    # 2a: Skip if busy with an active lease or marked in_use
    naive_now = datetime.now(timezone.utc).replace(tzinfo=None)
    active_lease = (
        db.query(DeviceLease)
        .filter(
            DeviceLease.device_id == device.id,
            DeviceLease.expires_at > naive_now,
        )
        .first()
    )
    if active_lease is not None or device.status == "in_use":
        logger.info(
            "Watchdog: skipping device %s (status=%s, lease=%s)",
            device.id,
            device.status,
            bool(active_lease),
        )
        return

    # 2b: Probe device
    res = client.probe_device(device=device, wake=False)
    status = res.get("status")
    if status in ("runner_unreachable", "missing_configuration"):
        logger.warning(
            "Watchdog: runner unreachable/misconfigured for device %s (%s): %s",
            device.id,
            device.name,
            res.get("detail") or status,
        )
        return

    adb_connected = bool(res.get("adb_connected"))
    portal_ready = bool(res.get("portal_ready"))
    is_healthy = adb_connected and portal_ready

    # 2c: Incident marker inspection
    marker_key = f"phone_watchdog:{device.id}"
    marker = (
        db.query(OperationalNotification)
        .filter(OperationalNotification.dedupe_key == marker_key)
        .first()
    )
    has_open_marker = marker is not None and marker.state == "open"

    if is_healthy:
        if has_open_marker:
            # 2f: Healthy and an open marker exists → status "available", marker resolved, alert
            device.status = "available"
            marker.state = "resolved"
            marker.message = "watchdog_maintenance:resolved"
            db.commit()

            msg = f"Phone {device.name} is healthy again; phone deliveries resumed."
            alert_key = f"phone_watchdog_resolved:{device.id}:{marker.id}"
            sent_ok, _ = notify_owner_via_hermes(
                msg,
                dedupe_key=alert_key,
                db=db,
                runner_client=client,
            )
            if not sent_ok:
                send_telegram_fallback(msg, db)
        else:
            # Healthy without an open marker:
            # If owner set maintenance manually, leave untouched
            return

    else:
        # Device is unhealthy
        if device.status == "available":
            # 2d: Unhealthy and available → set maintenance, create/refresh marker (open), commit
            device.status = "maintenance"
            now_iso = datetime.now(timezone.utc).isoformat()
            marker_was_open = has_open_marker
            if marker is None:
                marker = OperationalNotification(
                    dedupe_key=marker_key,
                    kind="phone_watchdog",
                    message="watchdog_maintenance:open",
                    state="open",
                    attempt_count=1,
                    last_error=now_iso,
                )
                db.add(marker)
            else:
                marker.state = "open"
                marker.message = "watchdog_maintenance:open"
                marker.attempt_count = (marker.attempt_count or 0) + 1
                marker.last_error = now_iso
            db.commit()

            # Attempt routine recovery ONCE
            client.recover_device(
                device=device,
                recovery_class="routine",
                reason="portal_unready",
            )

            # Probe again
            res2 = client.probe_device(device=device, wake=False)
            is_healthy2 = bool(res2.get("adb_connected")) and bool(res2.get("portal_ready"))
            if is_healthy2:
                # Recover succeeded: back to available, close marker, 0 alerts
                device.status = "available"
                marker.state = "resolved"
                marker.message = "watchdog_maintenance:resolved"
                db.commit()
            else:
                # Still unhealthy → send ONE owner alert (only when marker just created/opened in this tick)
                if not marker_was_open:
                    if not bool(res2.get("adb_connected")):
                        short_reason = "device unreachable via ADB"
                    elif not bool(res2.get("portal_ready")):
                        short_reason = "Mobilerun Portal accessibility unready"
                    else:
                        short_reason = str(res2.get("status") or "portal accessibility unready")

                    msg = (
                        f"Phone {device.name} ({device.adb_serial}) cannot publish: {short_reason}. "
                        "Phone deliveries are paused (waiting) until it is fixed. "
                        "Fix on the phone: Settings > Accessibility > Mobilerun Portal > On; "
                        "and Battery > Mobilerun Portal > No restrictions."
                    )
                    alert_key = f"phone_watchdog_alert:{device.id}:{marker.id}"
                    sent_ok, _ = notify_owner_via_hermes(
                        msg,
                        dedupe_key=alert_key,
                        db=db,
                        runner_client=client,
                    )
                    if not sent_ok:
                        send_telegram_fallback(msg, db)

        elif has_open_marker:
            # 2e: Unhealthy and an open marker exists → try recover_device at most once per 30 minutes
            last_attempt_dt = None
            if marker.last_error:
                try:
                    last_attempt_dt = datetime.fromisoformat(marker.last_error)
                    if last_attempt_dt.tzinfo is not None:
                        last_attempt_dt = last_attempt_dt.astimezone(timezone.utc).replace(tzinfo=None)
                except Exception:
                    last_attempt_dt = None

            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            can_retry = last_attempt_dt is None or (now_naive - last_attempt_dt) >= timedelta(minutes=30)
            if can_retry:
                client.recover_device(
                    device=device,
                    recovery_class="routine",
                    reason="portal_unready",
                )
                marker.last_error = datetime.now(timezone.utc).isoformat()
                marker.attempt_count = (marker.attempt_count or 0) + 1
                db.commit()

                res2 = client.probe_device(device=device, wake=False)
                is_healthy2 = bool(res2.get("adb_connected")) and bool(res2.get("portal_ready"))
                if is_healthy2:
                    device.status = "available"
                    marker.state = "resolved"
                    marker.message = "watchdog_maintenance:resolved"
                    db.commit()
                    msg = f"Phone {device.name} is healthy again; phone deliveries resumed."
                    alert_key = f"phone_watchdog_resolved:{device.id}:{marker.id}"
                    sent_ok, _ = notify_owner_via_hermes(
                        msg,
                        dedupe_key=alert_key,
                        db=db,
                        runner_client=client,
                    )
                    if not sent_ok:
                        send_telegram_fallback(msg, db)
        else:
            # Unhealthy, but no open marker and not available (e.g. owner manual maintenance) → untouched
            return


@celery_app.task(name="app.tasks.phone_watchdog.check_phone_devices", bind=True, ignore_result=True)
def check_phone_devices(self: Any = None, client: Any = None, db: Session | None = None) -> None:
    """Periodic watchdog checking phone availability and portal readiness."""
    should_close = False
    if db is None:
        db = SessionLocal()
        should_close = True

    try:
        if client is None:
            client = PhoneRunnerClient()

        devices = (
            db.query(PhoneDevice)
            .join(DestinationDeviceAssignment, DestinationDeviceAssignment.device_id == PhoneDevice.id)
            .join(SocialDestination, SocialDestination.id == DestinationDeviceAssignment.destination_id)
            .filter(
                DestinationDeviceAssignment.is_active == True,
                SocialDestination.is_active == True,
                SocialDestination.mode.in_(["phone", "mixed"]),
            )
            .distinct()
            .order_by(PhoneDevice.id)
            .all()
        )

        for device in devices:
            try:
                _check_single_device(db, client, device)
            except Exception as exc:
                logger.error(
                    "Watchdog error checking device id %s: %s",
                    device.id,
                    sanitize_error(str(exc)),
                )
                db.rollback()
    finally:
        if should_close:
            db.close()
