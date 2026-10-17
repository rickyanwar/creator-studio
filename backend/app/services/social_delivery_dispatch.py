"""Social delivery dispatch service module (S1b).

Manages delivery ledger dispatch, atomic claims, device leases, Repliz Page and
Phone Profile adapters, timeout/uncertainty handling, and paced backlog draining.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
import os
import re
import subprocess
from typing import Any, Sequence
import uuid

import httpx
from sqlalchemy import and_, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    PublishDelivery,
    RecoveryIncident,
    SocialDestination,
)
from app.services.repliz_client import (
    format_schedule_at,
    get_repliz_client_from_db,
)
from app.services.social_delivery_policy import (
    approval_state,
    assert_publish_identity,
    compute_next_slot,
    fallback_allowed,
)

logger = logging.getLogger(__name__)


@dataclass
class PhonePublishResult:
    """Publish result from PhoneRunnerClient."""

    status: str  # "published" | "waiting_for_device" | "uncertain" | "failed"
    facebook_post_id: str | None = None
    permalink: str | None = None
    actor_id: str | None = None
    publish_may_have_started: bool = False
    error_code: str | None = None
    evidence: dict[str, Any] | None = None
    stages: list[dict] = field(default_factory=list)


# PhoneRunnerClient is implemented by P1 in parallel
try:
    from app.services.phone_runner_client import (
        PhonePublishResult,
        PhoneRecoveryResult,
        PhoneRunnerClient,
        sanitize_error,
    )
except ImportError:
    PhoneRunnerClient = None
    PhonePublishResult = None
    PhoneRecoveryResult = None

    def sanitize_error(text: str, max_length: int = 256) -> str:
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


def _now(now_utc: datetime | None = None) -> datetime:
    if now_utc is None:
        return datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def claim_paced_delivery_for_dispatch(
    db: Session,
    delivery_id: int,
    eligible_states: Sequence[str] | None = None,
    now_utc: datetime | None = None,
) -> tuple[PublishDelivery | None, str | None]:
    """Atomically claim a delivery for dispatch under destination row lock.

    Serializes actual-send decisions and claims per destination.
    Enforces:
    1. Consistent lock ordering: SocialDestination -> PublishDelivery.
    2. Re-verifies fresh candidate state (.populate_existing) under lock.
    3. Blocks adjacent immediate live sends if another immediate send is currently 'publishing'.
    4. Safe minGap guard: queries recent live sends using scalar / EXISTS logic (no PG NULLS FIRST shadowing).
    5. Excludes future Repliz pre-schedule submission times from live-send guard.
    6. Counts recent immediate Repliz accepted status (repliz_scheduled) as actual live sends.
    7. Clean lock release via db.commit() on skip or claim.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)

    if eligible_states is None:
        eligible_states = ["scheduled", "waiting_for_slot", "waiting_for_device"]

    deliv_stub = (
        db.query(PublishDelivery.id, PublishDelivery.destination_id)
        .filter(PublishDelivery.id == delivery_id)
        .first()
    )
    if not deliv_stub or deliv_stub.destination_id is None:
        return None, None
    dest_id = deliv_stub.destination_id

    # 1. Lock destination first (dest -> delivery to prevent deadlocks)
    dest = (
        db.query(SocialDestination)
        .filter_by(id=dest_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not dest or not dest.is_active:
        db.commit()
        return None, None

    # 2. Lock candidate delivery under destination lock
    candidate = (
        db.query(PublishDelivery)
        .filter(
            PublishDelivery.id == delivery_id,
            PublishDelivery.destination_id == dest_id,
        )
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not candidate:
        db.commit()
        return None, None

    # Verify candidate fresh state
    if candidate.state not in eligible_states:
        db.commit()
        return None, None
    if candidate.claim_token is not None:
        db.commit()
        return None, None
    if candidate.uncertain_since is not None or candidate.state == "uncertain":
        db.commit()
        return None, None
    if candidate.facebook_post_id is not None or candidate.repliz_schedule_id is not None:
        db.commit()
        return None, None

    # Phone deliveries must wait until due slot
    if candidate.intended_method == "phone":
        if candidate.scheduled_for and candidate.scheduled_for > naive_now:
            db.commit()
            return None, None

    is_immediate = (
        candidate.intended_method == "phone"
        or candidate.scheduled_for is None
        or candidate.scheduled_for <= naive_now
    )

    if is_immediate:
        # A. Treat another in-progress immediate Phone/live send as blocking regardless of minGap
        active_immediate = (
            db.query(PublishDelivery.id)
            .filter(
                PublishDelivery.destination_id == dest_id,
                PublishDelivery.id != candidate.id,
                PublishDelivery.state == "publishing",
                or_(
                    PublishDelivery.intended_method == "phone",
                    PublishDelivery.scheduled_for.is_(None),
                    PublishDelivery.scheduled_for <= naive_now,
                ),
            )
            .first()
        )
        if active_immediate:
            db.commit()
            return None, None

        # B. Check min_gap_minutes against recent live sends
        min_gap_min = getattr(dest, "min_gap_minutes", None) or 0
        if min_gap_min > 0:
            gap_cutoff = naive_now - timedelta(minutes=float(min_gap_min))

            recent_live_send = (
                db.query(PublishDelivery.id)
                .filter(
                    PublishDelivery.destination_id == dest_id,
                    PublishDelivery.id != candidate.id,
                    or_(
                        # 1. Phone or direct live send published recently
                        and_(
                            PublishDelivery.state == "published",
                            or_(
                                PublishDelivery.intended_method.is_(None),
                                PublishDelivery.intended_method != "repliz",
                            ),
                            func.coalesce(PublishDelivery.published_at, PublishDelivery.claimed_at) >= gap_cutoff,
                            func.coalesce(PublishDelivery.published_at, PublishDelivery.claimed_at) <= naive_now,
                        ),
                        # 2. Immediate Repliz accepted/published recently (exclude future pre-schedules)
                        and_(
                            PublishDelivery.state.in_(["published", "repliz_scheduled"]),
                            PublishDelivery.intended_method == "repliz",
                            func.coalesce(PublishDelivery.published_at, PublishDelivery.claimed_at) >= gap_cutoff,
                            func.coalesce(PublishDelivery.published_at, PublishDelivery.claimed_at) <= naive_now,
                            or_(
                                PublishDelivery.scheduled_for.is_(None),
                                PublishDelivery.scheduled_for <= PublishDelivery.published_at,
                                PublishDelivery.scheduled_for <= naive_now,
                            ),
                        ),
                    ),
                )
                .first()
            )
            if recent_live_send:
                db.commit()
                return None, None

    # Atomic claim write under lock
    token = uuid.uuid4().hex
    candidate.state = "publishing"
    candidate.claim_token = token
    candidate.claimed_at = naive_now
    candidate.updated_at = naive_now

    event = DeliveryEvent(
        delivery_id=candidate.id,
        event_type="claim_acquired",
        details={
            "claim_token": token,
            "eligible_states": list(eligible_states),
            "intended_method": candidate.intended_method,
            "is_immediate": is_immediate,
        },
        created_at=naive_now,
    )
    db.add(event)
    db.commit()
    return candidate, token


def claim_delivery_for_dispatch(
    db: Session,
    delivery_id: int,
    eligible_states: Sequence[str] | None = None,
    now_utc: datetime | None = None,
    paced: bool = False,
) -> tuple[PublishDelivery | None, str | None]:
    """Atomically claim a delivery for dispatch before any remote side effect.

    Sets state to 'publishing', records claim_token and claimed_at, commits immediately.
    Returns (delivery, claim_token) if claimed, else (None, None).
    """
    if paced:
        return claim_paced_delivery_for_dispatch(
            db,
            delivery_id=delivery_id,
            eligible_states=eligible_states,
            now_utc=now_utc,
        )

    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)
    token = uuid.uuid4().hex

    if eligible_states is None:
        eligible_states = ["scheduled", "waiting_for_slot", "waiting_for_device"]

    claimed = (
        db.query(PublishDelivery)
        .filter(
            PublishDelivery.id == delivery_id,
            PublishDelivery.state.in_(list(eligible_states)),
        )
        .update(
            {
                "state": "publishing",
                "claim_token": token,
                "claimed_at": naive_now,
                "updated_at": naive_now,
            },
            synchronize_session=False,
        )
    )
    if not claimed:
        db.rollback()
        return None, None

    event = DeliveryEvent(
        delivery_id=delivery_id,
        event_type="claim_acquired",
        details={"claim_token": token, "eligible_states": list(eligible_states)},
        created_at=naive_now,
    )
    db.add(event)
    db.commit()

    delivery = db.query(PublishDelivery).filter_by(id=delivery_id).first()
    return delivery, token



def recover_stale_deliveries_and_leases(
    db: Session,
    now_utc: datetime | None = None,
    stale_ttl_minutes: int = 45,
) -> int:
    """Mark stale publishing claims as uncertain and release expired leases.

    A worker process death mid-task leaves deliveries in 'publishing'. Stale
    claims become 'uncertain' to prevent automatic duplicate posting.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)
    cutoff = naive_now - timedelta(minutes=stale_ttl_minutes)

    stale_deliveries = (
        db.query(PublishDelivery)
        .filter(
            PublishDelivery.state == "publishing",
            PublishDelivery.claimed_at <= cutoff,
        )
        .all()
    )

    recovered_count = 0
    for d in stale_deliveries:
        d.state = "uncertain"
        d.uncertain_since = naive_now
        d.last_error = f"Stale claim expired after {stale_ttl_minutes} minutes without completion"
        db.add(
            DeliveryEvent(
                delivery_id=d.id,
                event_type="uncertain",
                details={
                    "reason": "stale_claim_worker_timeout",
                    "claimed_at": d.claimed_at.isoformat() if d.claimed_at else None,
                },
                created_at=naive_now,
            )
        )
        recovered_count += 1

    # Clear expired device leases
    expired_leases = (
        db.query(DeviceLease)
        .filter(DeviceLease.expires_at <= naive_now)
        .all()
    )
    for lease in expired_leases:
        if lease.delivery_id:
            deliv = db.query(PublishDelivery).filter_by(id=lease.delivery_id).first()
            if deliv and deliv.state == "publishing":
                deliv.state = "uncertain"
                deliv.uncertain_since = naive_now
                deliv.last_error = "Device lease expired while in publishing state"
                db.add(
                    DeliveryEvent(
                        delivery_id=deliv.id,
                        event_type="uncertain",
                        details={"reason": "stale_lease_worker_timeout"},
                        created_at=naive_now,
                    )
                )
        db.delete(lease)

    if recovered_count or expired_leases:
        db.commit()

    return recovered_count


def dispatch_repliz_delivery(
    db: Session,
    delivery: PublishDelivery,
    repliz_client: Any = None,
    now_utc: datetime | None = None,
) -> PublishDelivery:
    """Dispatch a delivery to Repliz API.

    Selects image/album/video method from immutable delivery snapshot.
    Saves repliz_schedule_id, actual_method, status 'repliz_scheduled'.
    Ambiguous timeout or 5xx marks 'uncertain' with no automatic retry.
    Profiles cannot enter this adapter.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)
    destination = delivery.destination or db.query(SocialDestination).filter_by(id=delivery.destination_id).first()

    if not destination or destination.kind == "facebook_profile":
        delivery.state = "failed"
        delivery.last_error = "Profiles cannot publish via Repliz"
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="failed",
                details={"reason": "profile_repliz_forbidden"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    if not destination.repliz_account_id:
        delivery.state = "failed"
        delivery.last_error = "Destination missing repliz_account_id"
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="failed",
                details={"reason": "missing_repliz_account_id"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    client = repliz_client or get_repliz_client_from_db(db)
    media_kind = delivery.media_kind
    media_refs = list(delivery.media_snapshot or [])
    caption = delivery.caption_snapshot or ""
    sched_dt = delivery.scheduled_for or naive_now
    schedule_at_str = format_schedule_at(sched_dt)

    try:
        # Increment attempts only on a real external call attempt
        delivery.attempt_count = (delivery.attempt_count or 0) + 1

        if media_kind == "video":
            response = client.create_video_schedule(
                account_id=destination.repliz_account_id,
                description=caption,
                video_url=media_refs[0] if media_refs else "",
                thumbnail_url="",
                title=delivery.title_snapshot or "",
                schedule_at=schedule_at_str,
            )
        elif media_kind == "album" and len(media_refs) >= 2:
            response = client.create_album_schedule(
                account_id=destination.repliz_account_id,
                description=caption,
                image_urls=media_refs,
                schedule_at=schedule_at_str,
            )
        else:
            response = client.create_image_schedule(
                account_id=destination.repliz_account_id,
                description=caption,
                image_url=media_refs[0] if media_refs else "",
                alt=delivery.alt_snapshot or "",
                schedule_at=schedule_at_str,
            )

        raw_id = (
            response.get("_id") or response.get("id") or response.get("scheduleId")
        )
        schedule_id = str(raw_id).strip() if raw_id and str(raw_id).strip() else None

        if schedule_id:
            delivery.repliz_schedule_id = schedule_id
            delivery.actual_method = "repliz"
            delivery.state = "repliz_scheduled"
            delivery.published_at = naive_now
            delivery.last_error = None

            db.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type="repliz_scheduled",
                    details={
                        "schedule_id": schedule_id,
                        "actual_method": "repliz",
                        "account_id": destination.repliz_account_id,
                    },
                    created_at=naive_now,
                )
            )
            db.commit()
        else:
            delivery.state = "uncertain"
            delivery.uncertain_since = naive_now
            delivery.last_error = "Repliz response missing schedule ID"
            db.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type="uncertain",
                    details={"reason": "missing_repliz_schedule_id"},
                    created_at=naive_now,
                )
            )
            db.commit()

    except httpx.HTTPStatusError as exc:
        db.rollback()
        status_code = exc.response.status_code
        delivery = db.query(PublishDelivery).filter_by(id=delivery.id).first()
        delivery.attempt_count = (delivery.attempt_count or 0) + 1
        if status_code >= 500:
            delivery.state = "uncertain"
            delivery.uncertain_since = naive_now
            delivery.last_error = f"Repliz 5xx server error: HTTP {status_code}"
            event_type = "uncertain"
        else:
            delivery.state = "failed"
            delivery.last_error = f"Repliz client error: HTTP {status_code}"
            event_type = "failed"

        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type=event_type,
                details={"status_code": status_code, "reason": "http_error"},
                created_at=naive_now,
            )
        )
        db.commit()

    except (httpx.TimeoutException, httpx.RequestError) as exc:
        db.rollback()
        exc_class = type(exc).__name__
        delivery = db.query(PublishDelivery).filter_by(id=delivery.id).first()
        delivery.attempt_count = (delivery.attempt_count or 0) + 1
        delivery.state = "uncertain"
        delivery.uncertain_since = naive_now
        delivery.last_error = f"Repliz network timeout/error: {exc_class}"
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="uncertain",
                details={"error_type": exc_class, "reason": "network_timeout"},
                created_at=naive_now,
            )
        )
        db.commit()

    except Exception as exc:
        db.rollback()
        exc_class = type(exc).__name__
        delivery = db.query(PublishDelivery).filter_by(id=delivery.id).first()
        delivery.attempt_count = (delivery.attempt_count or 0) + 1
        delivery.state = "uncertain"
        delivery.uncertain_since = naive_now
        delivery.last_error = f"Repliz dispatch exception: {exc_class}"
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="uncertain",
                details={"error_type": exc_class, "reason": "unexpected_exception"},
                created_at=naive_now,
            )
        )
        db.commit()

    return delivery


def select_and_lease_phone_device(
    db: Session,
    delivery: PublishDelivery,
    now_utc: datetime | None = None,
    lease_ttl_minutes: int = 45,
) -> tuple[PhoneDevice | None, str | None]:
    """Select priority 0 then verified backup phone and acquire exclusive lease.

    Validates stable Profile/Target IDs and calibration. Skips maintenance, in_use,
    and uncalibrated phones. Requires active, verified assignment with verified_at.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)
    destination = delivery.destination or db.query(SocialDestination).filter_by(id=delivery.destination_id).first()
    if not destination:
        return None, "Destination not found"

    assignments = (
        db.query(DestinationDeviceAssignment)
        .filter(
            DestinationDeviceAssignment.destination_id == destination.id,
            DestinationDeviceAssignment.is_active.is_(True),
        )
        .order_by(DestinationDeviceAssignment.priority.asc())
        .all()
    )
    if not assignments:
        return None, "No active device assignments for destination"

    for assignment in assignments:
        if not assignment.is_active or not assignment.verified_at:
            continue

        device = assignment.device
        if not device:
            continue

        if not getattr(device, "adb_serial", None):
            continue

        if device.status != "available":
            continue

        if not device.calibration_verified_at:
            continue

        # Match stable profile / target identities
        # ponytail: device default expected_profile_actor_id is calibration fallback; verified assignment roots take precedence
        try:
            if destination.kind == "facebook_profile":
                dest_actor = destination.facebook_actor_id
                if not dest_actor or not str(dest_actor).strip():
                    continue
                dest_actor = str(dest_actor).strip()
                if assignment.verified_profile_actor_id != dest_actor:
                    continue
                if assignment.verified_target_actor_id != dest_actor:
                    continue
                assert_publish_identity(
                    expected_profile_id=dest_actor,
                    observed_profile_id=assignment.verified_profile_actor_id,
                    target_kind="profile",
                    expected_page_id=None,
                    observed_page_id=None,
                    calibration_valid=True,
                )
            elif destination.kind == "facebook_page":
                parent = destination.parent_profile
                if parent is None and destination.parent_profile_id:
                    parent = db.query(SocialDestination).filter_by(id=destination.parent_profile_id).first()
                if not parent or parent.kind != "facebook_profile" or not parent.facebook_actor_id:
                    continue
                parent_actor = str(parent.facebook_actor_id).strip()
                page_actor = str(destination.facebook_actor_id).strip() if destination.facebook_actor_id else ""
                if not page_actor:
                    continue
                if assignment.verified_profile_actor_id != parent_actor:
                    continue
                if assignment.verified_target_actor_id != page_actor:
                    continue
                assert_publish_identity(
                    expected_profile_id=parent_actor,
                    observed_profile_id=assignment.verified_profile_actor_id,
                    target_kind="page",
                    expected_page_id=page_actor,
                    observed_page_id=assignment.verified_target_actor_id,
                    calibration_valid=True,
                )
            else:
                continue
        except ValueError:
            continue

        # Exclusive atomic device lease with row lock and IntegrityError guard
        try:
            locked_device = (
                db.query(PhoneDevice)
                .filter(PhoneDevice.id == device.id)
                .with_for_update()
                .first()
            )
            if not locked_device or locked_device.status != "available":
                continue

            lease = db.query(DeviceLease).filter_by(device_id=locked_device.id).first()
            if lease:
                if lease.expires_at > naive_now:
                    # Active lease held by another task
                    continue
                # Expired lease — reclaim
                lease.delivery_id = delivery.id
                lease.acquired_at = naive_now
                lease.expires_at = naive_now + timedelta(minutes=lease_ttl_minutes)
            else:
                lease = DeviceLease(
                    device_id=locked_device.id,
                    delivery_id=delivery.id,
                    acquired_at=naive_now,
                    expires_at=naive_now + timedelta(minutes=lease_ttl_minutes),
                )
                db.add(lease)

            db.commit()
            return locked_device, None
        except IntegrityError:
            db.rollback()
            continue

    return None, "No eligible and available phone device could be leased"


def release_device_lease(db: Session, device_id: int, delivery_id: int) -> None:
    """Release exclusive device lease upon safe completion."""
    db.query(DeviceLease).filter(
        DeviceLease.device_id == device_id,
        DeviceLease.delivery_id == delivery_id,
    ).delete(synchronize_session=False)
    db.commit()


def notify_owner_via_hermes(
    message: str,
    subprocess_runner: Any = None,
    dedupe_key: str | None = None,
    db: Session | None = None,
    runner_client: Any = None,
) -> tuple[bool, str]:
    """Send owner notice using host runner /notify endpoint or subprocess fallback in tests."""
    clean_msg = sanitize_error(message)
    sent_ok = False
    detail = ""

    if subprocess_runner is not None:
        hermes_bin = os.environ.get("HERMES_PATH", "hermes")
        cmd = [hermes_bin, "send", "-t", "telegram", clean_msg]  # real CLI syntax: hermes send -t <target> <message>
        try:
            proc = subprocess_runner(
                cmd,
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
                shell=False,
            )
            if proc.returncode == 0:
                sent_ok = True
                detail = "sent"
            else:
                err = sanitize_error(proc.stderr or proc.stdout or f"exit code {proc.returncode}")
                detail = f"failed: {err}"
        except subprocess.TimeoutExpired:
            detail = "timed out"
        except FileNotFoundError:
            detail = f"executable not found: {hermes_bin}"
        except Exception as exc:
            detail = sanitize_error(str(exc))
    else:
        client = runner_client
        if client is None and PhoneRunnerClient is not None:
            try:
                client = PhoneRunnerClient()
            except Exception:
                client = None
        if client is not None:
            sent_ok, detail = client.send_notification(clean_msg, channel="telegram")
        else:
            sent_ok = False
            detail = "PhoneRunnerClient not available"

    if db is not None:
        try:
            key = dedupe_key or f"notice:{uuid.uuid4().hex}"
            existing = db.query(OperationalNotification).filter_by(dedupe_key=key).first()
            now_dt = datetime.now(timezone.utc).replace(tzinfo=None)
            if existing:
                existing.attempt_count = (existing.attempt_count or 0) + 1
                existing.state = "sent" if sent_ok else "failed"
                existing.last_error = None if sent_ok else detail
                if sent_ok:
                    existing.sent_at = now_dt
            else:
                notif = OperationalNotification(
                    dedupe_key=key,
                    kind="recovery_notice",
                    message=clean_msg,
                    state="sent" if sent_ok else "failed",
                    attempt_count=1,
                    last_error=None if sent_ok else detail,
                    sent_at=now_dt if sent_ok else None,
                )
                db.add(notif)
            db.commit()
        except Exception as exc:
            db.rollback()
            logger.warning("Failed to persist OperationalNotification: %s", exc)

    return sent_ok, detail


def reconcile_and_verify_absence(
    db: Session,
    delivery: PublishDelivery,
) -> bool:
    """Verify absence of duplicate delivery or post before permitting fallback."""
    if delivery.state == "uncertain" or delivery.uncertain_since is not None:
        return False
    if delivery.facebook_post_id or delivery.repliz_schedule_id:
        return False

    same_dest_dup = (
        db.query(PublishDelivery)
        .filter(
            PublishDelivery.publish_job_id == delivery.publish_job_id,
            PublishDelivery.destination_id == delivery.destination_id,
            PublishDelivery.id != delivery.id,
            PublishDelivery.state.in_(["published", "repliz_scheduled"]),
        )
        .first()
    )
    if same_dest_dup:
        return False

    if delivery.caption_sha256 and delivery.media_sha256:
        fingerprint_dup = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.destination_id == delivery.destination_id,
                PublishDelivery.id != delivery.id,
                PublishDelivery.caption_sha256 == delivery.caption_sha256,
                PublishDelivery.media_sha256 == delivery.media_sha256,
                PublishDelivery.state.in_(["published", "repliz_scheduled"]),
            )
            .first()
        )
        if fingerprint_dup:
            return False

    return True


def execute_device_recovery(
    db: Session,
    delivery: PublishDelivery,
    device: PhoneDevice | None = None,
    runner_client: Any = None,
    recovery_class: str = "routine",
    reason: str | None = None,
    owner_decision: str | None = None,
    now_utc: datetime | None = None,
    subprocess_runner: Any = None,
) -> RecoveryIncident | None:
    """Execute host recovery for assigned registered device and persist state."""
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)

    # 1. Identify registered device
    if device is None:
        if delivery.device_id:
            device = db.query(PhoneDevice).filter_by(id=delivery.device_id).first()
        if device is None:
            assignment = (
                db.query(DestinationDeviceAssignment)
                .filter_by(destination_id=delivery.destination_id, is_active=True)
                .order_by(DestinationDeviceAssignment.priority.asc())
                .first()
            )
            if assignment and assignment.verified_at and assignment.device_id:
                device = db.query(PhoneDevice).filter_by(id=assignment.device_id).first()

    if device is None or not device.adb_serial:
        return None

    # 2. Find or create RecoveryIncident
    incident = (
        db.query(RecoveryIncident)
        .filter(
            RecoveryIncident.device_id == device.id,
            RecoveryIncident.state.notin_(["resolved"]),
        )
        .order_by(RecoveryIncident.id.desc())
        .first()
    )

    if incident:
        effective_decision = incident.owner_decision or owner_decision
        if incident.state == "awaiting_approval" and recovery_class == "routine":
            recovery_class = "larger_repair"

        # Check silence window timing
        if incident.state == "awaiting_approval" and incident.auto_approve_after:
            if naive_now < incident.auto_approve_after and not effective_decision:
                db.add(
                    DeliveryEvent(
                        delivery_id=delivery.id,
                        event_type="recovery_awaiting_approval",
                        details={
                            "device_id": device.id,
                            "incident_id": incident.id,
                            "auto_approve_after": incident.auto_approve_after.isoformat(),
                            "owner_decision": None,
                            "state": "awaiting_approval",
                            "check_count": incident.check_count,
                            "hermes_attempt_count": incident.hermes_attempt_count,
                        },
                        created_at=naive_now,
                    )
                )
                db.commit()
                return incident
    else:
        incident = RecoveryIncident(
            device_id=device.id,
            delivery_id=delivery.id,
            state="checking",
            reason_code=reason or "device_offline",
            check_count=0,
            hermes_attempt_count=0,
            created_at=naive_now,
            updated_at=naive_now,
        )
        db.add(incident)
        db.flush()
        effective_decision = owner_decision

    # 3. Call PhoneRunnerClient
    client = runner_client
    if client is None:
        client = PhoneRunnerClient() if PhoneRunnerClient is not None else None

    if client is None:
        incident.state = "needs_human"
        incident.last_error = "PhoneRunnerClient not available"
        db.commit()
        return incident

    auto_approve_param = (
        incident.auto_approve_after.isoformat()
        if incident.auto_approve_after
        else None
    )
    owner_notified_param = (
        incident.owner_notified_at.isoformat()
        if incident.owner_notified_at
        else None
    )

    result = client.recover_device(
        device=device,
        delivery=delivery,
        recovery_class=recovery_class,
        reason=reason or incident.reason_code or "device_offline",
        owner_decision=effective_decision,
        auto_approve_after=auto_approve_param,
        owner_notified_at=owner_notified_param,
    )

    # 4. Update incident with result
    incident.delivery_id = delivery.id
    incident.check_count = (incident.check_count or 0) + (result.check_count or 1)
    incident.hermes_attempt_count = (incident.hermes_attempt_count or 0) + (
        result.hermes_attempt_count or 0
    )
    incident.last_attempt_at = naive_now
    incident.state = result.state

    if result.owner_notified_at:
        try:
            incident.owner_notified_at = datetime.fromisoformat(
                result.owner_notified_at.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            pass

    if result.auto_approve_after:
        try:
            incident.auto_approve_after = datetime.fromisoformat(
                result.auto_approve_after.replace("Z", "+00:00")
            ).replace(tzinfo=None)
        except Exception:
            pass

    if result.owner_decision:
        incident.owner_decision = result.owner_decision
    elif effective_decision:
        incident.owner_decision = effective_decision

    if result.status == "resolved" or result.state == "resolved":
        incident.state = "resolved"
        incident.resolved_at = naive_now
        incident.last_error = None
        device.status = "available"
        event_type = "recovery_resolved"
    elif result.state == "awaiting_approval":
        incident.last_error = sanitize_error(result.error or "")
        event_type = "recovery_awaiting_approval"
    elif result.state == "needs_human":
        incident.last_error = sanitize_error(result.error or "recovery_exhausted")
        event_type = "recovery_needs_human"
    else:
        incident.last_error = sanitize_error(result.error or "recovery_failed")
        event_type = "recovery_failed"

    db.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type=event_type,
            details={
                "device_id": device.id,
                "serial": device.adb_serial,
                "incident_id": incident.id,
                "recovery_class": result.recovery_class,
                "reason_code": result.reason_code,
                "status": result.status,
                "state": result.state,
                "check_count": incident.check_count,
                "hermes_attempt_count": incident.hermes_attempt_count,
                "owner_notified": result.notification_sent,
                "owner_decision": incident.owner_decision,
                "auto_approve_after": (
                    incident.auto_approve_after.isoformat()
                    if incident.auto_approve_after
                    else None
                ),
                "error": sanitize_error(result.error or ""),
            },
            created_at=naive_now,
        )
    )
    db.commit()
    return incident


def evaluate_and_execute_fallback(
    db: Session,
    delivery: PublishDelivery,
    incident: RecoveryIncident | None = None,
    repliz_client: Any = None,
    subprocess_runner: Any = None,
    now_utc: datetime | None = None,
) -> PublishDelivery:
    """Evaluate Page fallback gates and execute Repliz fallback when permitted."""
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)

    destination = delivery.destination or db.query(SocialDestination).filter_by(id=delivery.destination_id).first()
    if not destination:
        return delivery

    kind = "profile" if destination.kind == "facebook_profile" else "page"

    # 1. Profiles never fall back to Repliz
    if kind == "profile":
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={"reason": "profile_fallback_forbidden", "kind": "profile"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 2. Uncertain deliveries must never fall back
    if delivery.state == "uncertain" or delivery.uncertain_since is not None:
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={"reason": "uncertain_delivery_forbidden"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 3. Unsupported media waits
    supported = destination.supported_media or ["image", "album", "video"]
    if isinstance(supported, str):
        try:
            supported = json.loads(supported)
        except Exception:
            supported = [supported]

    content_supported = delivery.media_kind in supported and delivery.media_kind in ("image", "album", "video")
    if not content_supported:
        delivery.state = "waiting_for_slot"
        delivery.last_error = f"Unsupported media kind '{delivery.media_kind}' for Repliz fallback"
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={"reason": "unsupported_media", "media_kind": delivery.media_kind},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 4. Check incident exhaustion & repeated checks
    if incident is None:
        incident = (
            db.query(RecoveryIncident)
            .filter(RecoveryIncident.delivery_id == delivery.id)
            .order_by(RecoveryIncident.id.desc())
            .first()
        )

    incident_state = incident.state if incident else None
    check_count = incident.check_count if incident else 0
    is_exhausted = incident_state == "needs_human"
    repeated_checks = check_count >= 2

    if not is_exhausted or not repeated_checks:
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={
                    "reason": "recovery_not_exhausted_or_not_repeated",
                    "incident_state": incident_state,
                    "check_count": check_count,
                },
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 5. Connected Repliz account
    repliz_connected = bool(destination.repliz_account_id) and destination.is_active
    if not repliz_connected:
        delivery.state = "needs_human"
        delivery.last_error = "Repliz fallback not connected or destination inactive"
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={"reason": "repliz_not_connected"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 6. Absence verification & duplicate reconciliation
    absence_verified = reconcile_and_verify_absence(db, delivery)
    if not absence_verified:
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={"reason": "duplicate_reconciliation_failed"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 7. Check policy fallback_allowed
    if not fallback_allowed(
        kind=kind,
        incident_state=incident_state,
        uncertain=False,
        repliz_connected=True,
        content_supported=True,
        absence_verified=True,
    ):
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="fallback_skipped",
                details={"reason": "policy_disallowed"},
                created_at=naive_now,
            )
        )
        db.commit()
        return delivery

    # 8. Execute fallback
    prev_method = delivery.intended_method
    delivery.intended_method = "repliz"
    db.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type="fallback_to_repliz",
            details={
                "previous_method": prev_method,
                "incident_id": incident.id if incident else None,
                "repliz_account_id": destination.repliz_account_id,
            },
            created_at=naive_now,
        )
    )
    db.commit()

    # Notify owner through host Hermes path of fallback choice
    notif_msg = f"[FALLBACK] Delivery {delivery.id} for Page '{destination.name}' falling back to Repliz due to device recovery exhaustion."
    notif_ok, notif_err = notify_owner_via_hermes(
        notif_msg,
        subprocess_runner=subprocess_runner,
        dedupe_key=f"fallback:{delivery.id}",
        db=db,
    )
    db.add(
        DeliveryEvent(
            delivery_id=delivery.id,
            event_type="owner_notification_sent" if notif_ok else "owner_notification_failed",
            details={"channel": "telegram", "message": notif_msg, "status": "sent" if notif_ok else notif_err},
            created_at=naive_now,
        )
    )
    db.commit()

    # Dispatch to Repliz
    res_deliv = dispatch_repliz_delivery(db, delivery, repliz_client=repliz_client, now_utc=now)

    # Notify owner of outcome
    outcome_msg = f"[FALLBACK OUTCOME] Delivery {delivery.id} status is now {res_deliv.state}."
    notify_owner_via_hermes(
        outcome_msg,
        subprocess_runner=subprocess_runner,
        dedupe_key=f"fallback_outcome:{delivery.id}",
        db=db,
    )

    return res_deliv


def _compose_phone_last_error(base_error: str, stages: list[dict[str, Any]] | None) -> str:
    """Compose delivery.last_error from error_code and first failing stage."""
    failing: dict[str, Any] | None = None
    if isinstance(stages, list):
        for s in stages:
            if isinstance(s, dict) and not s.get("passed", True):
                failing = s
                break
    if not failing:
        return sanitize_error(base_error, max_length=300)

    parts: list[str] = [base_error.strip()] if base_error else []
    stage_name = str(failing.get("stage") or "").strip()
    if stage_name:
        parts.append(stage_name)
    stage_detail = str(failing.get("detail") or "").strip()
    if stage_detail:
        parts.append(stage_detail)

    composed = ": ".join([p for p in parts if p])
    return sanitize_error(composed, max_length=300)


def dispatch_phone_delivery(
    db: Session,
    delivery: PublishDelivery,
    runner_client: Any = None,
    repliz_client: Any = None,
    now_utc: datetime | None = None,
    subprocess_runner: Any = None,
    auto_recover: bool = True,
) -> PublishDelivery:
    """Dispatch delivery to PhoneRunnerClient with exclusive lease.

    Handles waiting_for_device, published, failed, uncertain statuses,
    host recovery invocation, and gated Repliz fallback.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)
    destination = delivery.destination or db.query(SocialDestination).filter_by(id=delivery.destination_id).first()

    device, err_reason = select_and_lease_phone_device(db, delivery, now_utc=now)
    if not device:
        delivery.state = "waiting_for_device"
        delivery.last_error = sanitize_error(err_reason or "")

        first_assignment = (
            db.query(DestinationDeviceAssignment)
            .filter_by(destination_id=delivery.destination_id, is_active=True)
            .order_by(DestinationDeviceAssignment.priority.asc())
            .first()
        )

        incident = None
        # Only recover if assignment is verified and registered, and failure is due to device availability
        if (
            auto_recover
            and first_assignment
            and first_assignment.verified_at
            and first_assignment.device_id
            and (first_assignment.device and first_assignment.device.status in ("maintenance", "in_use", "offline"))
        ):
            incident = execute_device_recovery(
                db,
                delivery,
                device=first_assignment.device,
                runner_client=runner_client,
                reason=err_reason or "device_unreachable",
                now_utc=now,
                subprocess_runner=subprocess_runner,
            )

        if incident and incident.state == "resolved":
            device, err_reason = select_and_lease_phone_device(db, delivery, now_utc=now)
            if not device:
                delivery.state = "waiting_for_device"
                delivery.last_error = sanitize_error(err_reason or "")
                db.commit()
                return delivery
        elif incident and incident.state == "needs_human":
            delivery = evaluate_and_execute_fallback(
                db,
                delivery,
                incident=incident,
                repliz_client=repliz_client,
                subprocess_runner=subprocess_runner,
                now_utc=now,
            )
            return delivery
        else:
            if not incident and first_assignment and first_assignment.device_id:
                inc = (
                    db.query(RecoveryIncident)
                    .filter(
                        RecoveryIncident.device_id == first_assignment.device_id,
                        RecoveryIncident.state.notin_(["resolved"]),
                    )
                    .first()
                )
                if inc:
                    inc.delivery_id = delivery.id
                    inc.check_count = (inc.check_count or 0) + 1
                    inc.last_attempt_at = naive_now
                    inc.last_error = sanitize_error(err_reason or "")
                else:
                    inc = RecoveryIncident(
                        device_id=first_assignment.device_id,
                        delivery_id=delivery.id,
                        state="checking",
                        reason_code=err_reason or "device_unreachable",
                        check_count=1,
                        last_attempt_at=naive_now,
                        last_error=sanitize_error(err_reason or ""),
                    )
                    db.add(inc)

            db.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type="waiting_for_device",
                    details={"reason": sanitize_error(err_reason or "")},
                    created_at=naive_now,
                )
            )
            db.commit()
            return delivery

    # Device leased successfully
    delivery.device_id = device.id
    main_profile = destination if destination.kind == "facebook_profile" else destination.parent_profile
    if main_profile is None and destination.parent_profile_id:
        main_profile = db.query(SocialDestination).filter_by(id=destination.parent_profile_id).first()

    client = runner_client
    if client is None:
        if PhoneRunnerClient is not None:
            client = PhoneRunnerClient()
        else:
            release_device_lease(db, device.id, delivery.id)
            delivery.state = "failed"
            delivery.last_error = "PhoneRunnerClient not available"
            db.commit()
            return delivery

    try:
        delivery.attempt_count = (delivery.attempt_count or 0) + 1
        result = client.publish_delivery(
            device=device,
            delivery=delivery,
            main_profile=main_profile,
            destination=destination,
        )

        if result.status == "published":
            post_id = result.facebook_post_id
            is_valid_post_id = bool(post_id and isinstance(post_id, str) and post_id.strip())
            is_matching_actor = bool(
                result.actor_id and destination.facebook_actor_id and
                str(result.actor_id).strip() == str(destination.facebook_actor_id).strip()
            )
            if is_valid_post_id and is_matching_actor:
                clean_post_id = post_id.strip()
                delivery.state = "published"
                delivery.actual_method = "phone"
                delivery.facebook_post_id = clean_post_id
                delivery.facebook_permalink = result.permalink
                delivery.published_at = naive_now
                delivery.last_error = None
                db.add(
                    DeliveryEvent(
                        delivery_id=delivery.id,
                        event_type="published",
                        details={
                            "facebook_post_id": clean_post_id,
                            "permalink": result.permalink,
                            "actual_method": "phone",
                            "actor_id": str(result.actor_id).strip(),
                        },
                        created_at=naive_now,
                    )
                )
            else:
                delivery.state = "uncertain"
                delivery.uncertain_since = naive_now
                if not is_valid_post_id:
                    reason = "missing_facebook_post_id"
                    delivery.last_error = "Phone publish reported success with missing or invalid Facebook post ID"
                else:
                    reason = "actor_id_mismatch"
                    delivery.last_error = "Phone publish reported success with mismatched actor ID"
                uncertain_details: dict[str, Any] = {
                    "reason": reason,
                    "publish_may_have_started": True,
                }
                if getattr(result, "stages", None):
                    uncertain_details["stages"] = result.stages
                db.add(
                    DeliveryEvent(
                        delivery_id=delivery.id,
                        event_type="uncertain",
                        details=uncertain_details,
                        created_at=naive_now,
                    )
                )
        elif result.status == "waiting_for_device":
            delivery.state = "waiting_for_device"
            delivery.last_error = _compose_phone_last_error(
                result.error_code or "Device not ready",
                getattr(result, "stages", None),
            )
            wfd_details: dict[str, Any] = {"error_code": sanitize_error(result.error_code or "")}
            if getattr(result, "stages", None):
                wfd_details["stages"] = result.stages
            db.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type="waiting_for_device",
                    details=wfd_details,
                    created_at=naive_now,
                )
            )
            if auto_recover:
                inc = execute_device_recovery(
                    db,
                    delivery,
                    device=device,
                    runner_client=runner_client,
                    reason=result.error_code or "device_not_ready",
                    now_utc=now,
                    subprocess_runner=subprocess_runner,
                )
                if inc and inc.state == "needs_human":
                    delivery = evaluate_and_execute_fallback(
                        db,
                        delivery,
                        incident=inc,
                        repliz_client=repliz_client,
                        subprocess_runner=subprocess_runner,
                        now_utc=now,
                    )
                    return delivery
        elif result.status == "uncertain" or result.publish_may_have_started:
            delivery.state = "uncertain"
            delivery.uncertain_since = naive_now
            delivery.last_error = sanitize_error(result.error_code or "Publish may have started; awaiting reconciliation")
            uncertain_details = {
                "error_code": sanitize_error(result.error_code or ""),
                "publish_may_have_started": True,
            }
            if getattr(result, "stages", None):
                uncertain_details["stages"] = result.stages
            db.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type="uncertain",
                    details=uncertain_details,
                    created_at=naive_now,
                )
            )
        else:  # failed
            delivery.state = "failed"
            delivery.last_error = _compose_phone_last_error(
                result.error_code or "Phone publish failed",
                getattr(result, "stages", None),
            )
            failed_details: dict[str, Any] = {"error_code": sanitize_error(result.error_code or "")}
            if getattr(result, "stages", None):
                failed_details["stages"] = result.stages
            db.add(
                DeliveryEvent(
                    delivery_id=delivery.id,
                    event_type="failed",
                    details=failed_details,
                    created_at=naive_now,
                )
            )

    except Exception as exc:
        exc_class = type(exc).__name__
        logger.error("Phone publish exception on delivery %d: %s", delivery.id, exc_class)
        delivery.state = "uncertain"
        delivery.uncertain_since = naive_now
        delivery.last_error = sanitize_error(f"Phone runner exception: {exc_class}")
        db.add(
            DeliveryEvent(
                delivery_id=delivery.id,
                event_type="uncertain",
                details={"error_type": exc_class, "reason": "phone_runner_exception"},
                created_at=naive_now,
            )
        )
    finally:
        release_device_lease(db, device.id, delivery.id)

    db.commit()
    return delivery


def _recovering_phone_destinations_query(
    db: Session,
    device_id: int | None = None,
):
    # IN scalar subquery avoids SELECT DISTINCT over SocialDestination.supported_media JSON
    eligible_ids_query = (
        db.query(SocialDestination.id)
        .join(DestinationDeviceAssignment, DestinationDeviceAssignment.destination_id == SocialDestination.id)
        .join(PhoneDevice, PhoneDevice.id == DestinationDeviceAssignment.device_id)
        .filter(
            SocialDestination.is_active.is_(True),
            DestinationDeviceAssignment.is_active.is_(True),
            PhoneDevice.status == "available",
            PhoneDevice.calibration_verified_at.isnot(None),
        )
    )
    if device_id is not None:
        eligible_ids_query = eligible_ids_query.filter(PhoneDevice.id == device_id)

    return db.query(SocialDestination).filter(SocialDestination.id.in_(eligible_ids_query)).order_by(SocialDestination.id.asc())


def replan_recovering_phone_deliveries(
    db: Session,
    device_id: int | None = None,
    now_utc: datetime | None = None,
) -> list[PublishDelivery]:
    """Replan waiting Phone deliveries into ordinary future slots upon device recovery.

    Respects per-destination daily_target and min_gap_minutes; avoids backlog bursts.
    Unsupported media remains waiting with no remote call.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)

    from app.services.publishing_schedule import (
        compute_audience_slot,
        get_destination_reservations_utc,
    )

    destinations = _recovering_phone_destinations_query(db, device_id=device_id).all()
    replanned: list[PublishDelivery] = []

    for dest in destinations:
        waiting = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.destination_id == dest.id,
                PublishDelivery.intended_method == "phone",
                PublishDelivery.state.in_(["waiting_for_device", "waiting_for_slot"]),
            )
            .order_by(PublishDelivery.created_at.asc(), PublishDelivery.id.asc())
            .all()
        )
        if not waiting:
            continue

        supported = dest.supported_media or []
        if isinstance(supported, str):
            try:
                supported = json.loads(supported)
            except Exception:
                supported = [supported]

        fanpage = getattr(dest, "content_project", None)
        if fanpage is None and dest.content_project_id:
            fanpage = db.query(TargetFanpage).filter_by(id=dest.content_project_id).first()
        sleep_start = fanpage.publish_sleep_start_hour if fanpage else None
        sleep_end = fanpage.publish_sleep_end_hour if fanpage else None

        supported_waiting: list[PublishDelivery] = []
        for deliv in waiting:
            if deliv.media_kind in supported:
                supported_waiting.append(deliv)
            else:
                deliv.state = "waiting_for_slot"
                deliv.last_error = f"Unsupported media kind '{deliv.media_kind}'"

        if not supported_waiting:
            continue

        all_reservations = get_destination_reservations_utc(db, dest)
        existing_slots_utc = list(all_reservations)
        for w in supported_waiting:
            if w.scheduled_for is not None:
                stale_ts = w.scheduled_for.replace(tzinfo=timezone.utc)
                if stale_ts in existing_slots_utc:
                    existing_slots_utc.remove(stale_ts)

        for deliv in supported_waiting:
            next_slot = compute_audience_slot(
                now_utc=now,
                timezone_name=dest.timezone,
                daily_target=dest.daily_target,
                min_gap_minutes=dest.min_gap_minutes,
                existing_slots_utc=existing_slots_utc,
                sleep_start_hour=sleep_start,
                sleep_end_hour=sleep_end,
                namespace=f"dest-{dest.id}",
            )
            existing_slots_utc.append(next_slot)
            naive_slot = next_slot.astimezone(timezone.utc).replace(tzinfo=None)

            deliv.scheduled_for = naive_slot
            deliv.state = "scheduled"
            deliv.last_error = None
            # The failed attempt never started publishing (waiting_for_device), so drop its claim:
            # claim_paced_delivery_for_dispatch skips any row that still has a claim_token, which
            # left recovered deliveries stuck in "scheduled" forever (delivery 152, 2026-10-17).
            deliv.claim_token = None
            deliv.claimed_at = None
            db.add(
                DeliveryEvent(
                    delivery_id=deliv.id,
                    event_type="rescheduled",
                    details={
                        "scheduled_for": naive_slot.isoformat(),
                        "reason": "phone_device_recovered",
                    },
                    created_at=naive_now,
                )
            )
            replanned.append(deliv)

    if replanned:
        db.commit()

    return replanned


def replan_stale_scheduled_deliveries(
    db: Session,
    now_utc: datetime | None = None,
) -> list[PublishDelivery]:
    """Replan untouched scheduled deliveries overdue by >10 minutes.

    Prevents immediate burst execution after server maintenance or downtime.
    Serializes per-destination using row lock. Re-checks fresh row state
    and external IDs to ensure concurrent claims are not overwritten. Moves
    untouched records to the next valid paced future slot and synchronizes
    parent PublishJob.scheduled_for.
    """
    from app.services.publishing_schedule import (
        compute_audience_slot,
        get_destination_reservations_utc,
    )

    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)
    stale_cutoff = naive_now - timedelta(minutes=10)

    stale_deliveries = (
        db.query(PublishDelivery)
        .filter(
            PublishDelivery.state == "scheduled",
            PublishDelivery.attempt_count == 0,
            PublishDelivery.claim_token.is_(None),
            PublishDelivery.repliz_schedule_id.is_(None),
            PublishDelivery.facebook_post_id.is_(None),
            PublishDelivery.uncertain_since.is_(None),
            PublishDelivery.scheduled_for < stale_cutoff,
        )
        .order_by(PublishDelivery.scheduled_for.asc(), PublishDelivery.id.asc())
        .all()
    )
    if not stale_deliveries:
        return []

    by_dest: dict[int, list[int]] = {}
    for deliv in stale_deliveries:
        by_dest.setdefault(deliv.destination_id, []).append(deliv.id)

    replanned: list[PublishDelivery] = []
    for dest_id in sorted(by_dest):
        deliv_ids = by_dest[dest_id]
        destination = (
            db.query(SocialDestination)
            .filter_by(id=dest_id)
            .populate_existing()
            .with_for_update()
            .first()
        )
        if not destination or not destination.is_active:
            continue

        fanpage = getattr(destination, "content_project", None)
        if fanpage is None and destination.content_project_id:
            fanpage = db.query(TargetFanpage).filter_by(id=destination.content_project_id).first()
        sleep_start = fanpage.publish_sleep_start_hour if fanpage else None
        sleep_end = fanpage.publish_sleep_end_hour if fanpage else None

        # Re-fetch candidate deliveries with row lock and populate_existing
        locked_delivs = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.id.in_(deliv_ids),
                PublishDelivery.destination_id == dest_id,
            )
            .populate_existing()
            .with_for_update()
            .order_by(PublishDelivery.scheduled_for.asc(), PublishDelivery.id.asc())
            .all()
        )

        eligible_delivs: list[PublishDelivery] = []
        for deliv in locked_delivs:
            if (
                deliv.state == "scheduled"
                and deliv.attempt_count == 0
                and deliv.claim_token is None
                and deliv.repliz_schedule_id is None
                and deliv.facebook_post_id is None
                and deliv.uncertain_since is None
                and deliv.scheduled_for is not None
                and deliv.scheduled_for < stale_cutoff
            ):
                eligible_delivs.append(deliv)

        if not eligible_delivs:
            continue

        all_reservations = get_destination_reservations_utc(db, destination)
        existing_slots_utc = list(all_reservations)
        for deliv in eligible_delivs:
            stale_ts = deliv.scheduled_for.replace(tzinfo=timezone.utc)
            if stale_ts in existing_slots_utc:
                existing_slots_utc.remove(stale_ts)

        for deliv in eligible_delivs:
            old_scheduled = deliv.scheduled_for
            next_slot = compute_audience_slot(
                now_utc=now,
                timezone_name=destination.timezone,
                daily_target=destination.daily_target,
                min_gap_minutes=destination.min_gap_minutes,
                existing_slots_utc=existing_slots_utc,
                sleep_start_hour=sleep_start,
                sleep_end_hour=sleep_end,
                namespace=f"dest-{destination.id}",
            )
            existing_slots_utc.append(next_slot)
            naive_slot = next_slot.astimezone(timezone.utc).replace(tzinfo=None)

            deliv.scheduled_for = naive_slot
            deliv.last_error = None

            # Synchronize parent PublishJob.scheduled_for if matching old slot or None
            job = db.query(PublishJob).filter_by(id=deliv.publish_job_id).first()
            if job and (job.scheduled_for == old_scheduled or job.scheduled_for is None):
                job.scheduled_for = naive_slot

            db.add(
                DeliveryEvent(
                    delivery_id=deliv.id,
                    event_type="rescheduled",
                    details={
                        "reason": "overdue_untouched_replan",
                        "previous_scheduled_for": old_scheduled.isoformat() if old_scheduled else None,
                        "scheduled_for": naive_slot.isoformat(),
                        "publish_job_id": deliv.publish_job_id,
                    },
                    created_at=naive_now,
                )
            )
            replanned.append(deliv)

    if replanned:
        db.commit()

    return replanned


def dispatch_due_deliveries(
    db: Session,
    job_id: int | None = None,
    now_utc: datetime | None = None,
    repliz_client: Any = None,
    runner_client: Any = None,
    subprocess_runner: Any = None,
) -> list[PublishDelivery]:
    """Sweep and dispatch due deliveries.

    - Repliz deliveries may be submitted ahead of scheduled_for using scheduleAt.
    - Phone deliveries must wait until scheduled_for <= now.
    - Updates PublishJob status when all deliveries achieve accepted status.
    """
    now = _now(now_utc)
    naive_now = now.replace(tzinfo=None)

    recover_stale_deliveries_and_leases(db, now_utc=now)
    replan_recovering_phone_deliveries(db, now_utc=now)
    replan_stale_scheduled_deliveries(db, now_utc=now)

    # Check awaiting approval incidents whose 15-minute silence window has passed
    awaiting_incidents = (
        db.query(RecoveryIncident)
        .filter(
            RecoveryIncident.state == "awaiting_approval",
            RecoveryIncident.auto_approve_after.isnot(None),
            RecoveryIncident.auto_approve_after <= naive_now,
        )
        .all()
    )
    for inc in awaiting_incidents:
        if inc.delivery_id:
            deliv = db.query(PublishDelivery).filter_by(id=inc.delivery_id).first()
            if deliv and deliv.state in ("waiting_for_device", "waiting_for_slot"):
                inc_after = execute_device_recovery(
                    db,
                    deliv,
                    runner_client=runner_client,
                    recovery_class="larger_repair",
                    now_utc=now,
                    subprocess_runner=subprocess_runner,
                )
                if inc_after and inc_after.state == "needs_human":
                    evaluate_and_execute_fallback(
                        db,
                        deliv,
                        incident=inc_after,
                        repliz_client=repliz_client,
                        subprocess_runner=subprocess_runner,
                        now_utc=now,
                    )

    query = db.query(PublishDelivery).filter(
        PublishDelivery.state == "scheduled"
    )
    if job_id is not None:
        query = query.filter(PublishDelivery.publish_job_id == job_id)

    candidates = query.order_by(PublishDelivery.scheduled_for.asc()).all()
    processed: list[PublishDelivery] = []

    for candidate in candidates:
        delivery, token = claim_paced_delivery_for_dispatch(
            db,
            candidate.id,
            eligible_states=["scheduled"],
            now_utc=now,
        )
        if not delivery:
            continue

        if delivery.intended_method == "repliz":
            result_deliv = dispatch_repliz_delivery(
                db,
                delivery,
                repliz_client=repliz_client,
                now_utc=now,
            )
        else:
            result_deliv = dispatch_phone_delivery(
                db,
                delivery,
                runner_client=runner_client,
                repliz_client=repliz_client,
                now_utc=now,
                subprocess_runner=subprocess_runner,
            )
        processed.append(result_deliv)

    # Check and update parent PublishJob status
    touched_job_ids = {d.publish_job_id for d in processed}
    if job_id is not None:
        touched_job_ids.add(job_id)

    for jid in touched_job_ids:
        all_deliveries = (
            db.query(PublishDelivery)
            .filter_by(publish_job_id=jid)
            .all()
        )
        if not all_deliveries:
            continue

        job = db.query(PublishJob).filter_by(id=jid).first()
        if not job:
            continue

        all_accepted = all(
            d.state in ("repliz_scheduled", "published") for d in all_deliveries
        )

        if all_accepted:
            job.status = PublishJobStatus.published
            job.published_at = naive_now
            # Copy compatibility schedule fields if exactly one Page Repliz destination
            repliz_delivs = [d for d in all_deliveries if d.actual_method == "repliz"]
            if len(all_deliveries) == 1 and len(repliz_delivs) == 1:
                rd = repliz_delivs[0]
                job.repliz_schedule_id = rd.repliz_schedule_id
                job.scheduled_for = rd.scheduled_for
        elif job.status == PublishJobStatus.publishing:
            # Release back to pending_publish for the recovery sweep
            job.status = PublishJobStatus.pending_publish

        db.commit()

    return processed
