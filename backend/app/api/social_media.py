"""Social media destinations, devices, and assignments API router."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import json
import logging
import os
import re
from typing import Optional
import zoneinfo

from fastapi import APIRouter, Header, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import joinedload, selectinload

from app.api.deps import CurrentUser, DB
from app.config import get_settings
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    DeviceLease,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.schemas.social_media import (
    DeliveryActivityOut,
    DeliveryEventOut,
    DestinationDeviceAssignmentCreate,
    DestinationDeviceAssignmentOut,
    DestinationDeviceAssignmentVerify,
    DeviceDryRunRequest,
    PhoneDeviceCreate,
    PhoneDeviceDiscoveryResponse,
    PhoneDeviceOut,
    PhoneDeviceUpdate,
    SchedulePreviewResponse,
    SchedulePreviewSlot,
    SocialDestinationCreate,
    SocialDestinationOut,
    SocialDestinationUpdate,
)
from app.services.phone_runner_client import PhoneRunnerClient
from app.services.publishing_schedule import (
    compute_audience_slot,
    get_destination_reservations_utc,
)
from app.services.social_delivery_policy import choose_method_for_slot, validate_route

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social-media", tags=["social-media"])

_NONTERMINAL_STATES = (
    "waiting_for_slot",
    "scheduled",
    "publishing",
    "waiting_for_device",
    "uncertain",
    "needs_human",
    "repliz_scheduled",
)

_FB_KINDS = {"facebook_page", "facebook_profile"}
_NON_FB_KINDS = {"instagram", "youtube", "threads", "x"}
_ALL_VALID_KINDS = _FB_KINDS | _NON_FB_KINDS


def _normalize_kind(kind: str | None) -> str | None:
    if kind is None:
        return None
    k = kind.strip().lower()
    if k in ("profile", "facebook_profile"):
        return "facebook_profile"
    if k in ("page", "facebook_page"):
        return "facebook_page"
    if k in _NON_FB_KINDS:
        return k
    return k


# ── Destinations CRUD ─────────────────────────────────────────────────────────


@router.get("/destinations", response_model=list[SocialDestinationOut])
def list_destinations(
    db: DB,
    _: CurrentUser,
    content_project_id: Optional[int] = Query(None),
    kind: Optional[str] = Query(None),
    is_active: Optional[bool] = Query(None),
):
    """List social destinations with optional filtering."""
    q = db.query(SocialDestination)
    if content_project_id is not None:
        q = q.filter(SocialDestination.content_project_id == content_project_id)
    if kind is not None:
        norm_k = _normalize_kind(kind)
        q = q.filter(SocialDestination.kind == norm_k)
    if is_active is not None:
        q = q.filter(SocialDestination.is_active == is_active)
    return q.order_by(SocialDestination.name).all()


@router.get("/destinations/{destination_id}", response_model=SocialDestinationOut)
def get_destination(destination_id: int, db: DB, _: CurrentUser):
    """Get single destination by ID."""
    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")
    return dest


@router.get(
    "/destinations/{destination_id}/schedule-preview",
    response_model=SchedulePreviewResponse,
)
def get_destination_schedule_preview(
    destination_id: int,
    db: DB,
    _: CurrentUser,
    limit: int = Query(6, ge=1, le=12),
):
    """Read-only preview of next available hypothetical publishing slots for a destination."""
    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")

    project = db.query(TargetFanpage).filter_by(id=dest.content_project_id).first()
    target_country = project.target_country if project else None
    sleep_start = project.publish_sleep_start_hour if project else None
    sleep_end = project.publish_sleep_end_hour if project else None

    tz_name = (dest.timezone or (project.timezone if project else None) or "Europe/London").strip()
    try:
        dest_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid destination timezone: {tz_name}")

    daily_target = int(dest.daily_target) if dest.daily_target and dest.daily_target > 0 else 1
    min_gap = int(dest.min_gap_minutes) if dest.min_gap_minutes is not None and dest.min_gap_minutes >= 0 else 5

    try:
        existing_reservations = get_destination_reservations_utc(db, dest)
    except Exception:
        raise HTTPException(status_code=503, detail="Unable to retrieve existing publishing reservations")

    slots_utc: list[datetime] = []
    slots_local: list[str] = []
    preview_slots: list[SchedulePreviewSlot] = []

    now_utc = datetime.now(timezone.utc)
    simulated_reservations = list(existing_reservations)

    for _ in range(limit):
        try:
            next_slot = compute_audience_slot(
                now_utc=now_utc,
                timezone_name=tz_name,
                daily_target=daily_target,
                min_gap_minutes=min_gap,
                existing_slots_utc=simulated_reservations,
                sleep_start_hour=sleep_start,
                sleep_end_hour=sleep_end,
                namespace=f"dest-{dest.id}",
            )
        except ValueError as e:
            raise HTTPException(status_code=422, detail=f"Invalid schedule parameters: {e}")
        except Exception:
            raise HTTPException(status_code=503, detail="Unable to calculate upcoming schedule slots")

        simulated_reservations.append(next_slot)
        slots_utc.append(next_slot)

        loc_str = next_slot.astimezone(dest_tz).isoformat()
        slots_local.append(loc_str)
        preview_slots.append(SchedulePreviewSlot(slot_utc=next_slot, slot_local=loc_str))

    return SchedulePreviewResponse(
        slots_utc=slots_utc,
        slots_local=slots_local,
        slots=preview_slots,
        timezone=tz_name,
        target_country=target_country,
        daily_limit=daily_target,
        min_gap_minutes=min_gap,
        sleep_start=sleep_start,
        sleep_end=sleep_end,
        basis="local_time_estimate",
        note="Estimated local activity windows; no account audience Insights connected.",
    )


@router.post(
    "/destinations",
    response_model=SocialDestinationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_destination(payload: SocialDestinationCreate, db: DB, _: CurrentUser):
    """Create a new social destination with route policy and hierarchy validation."""
    project = db.query(TargetFanpage).filter_by(id=payload.content_project_id).first()
    if not project:
        raise HTTPException(status_code=400, detail="Content project not found")

    db_kind = _normalize_kind(payload.kind)
    if db_kind not in _ALL_VALID_KINDS:
        raise HTTPException(status_code=400, detail=f"Unsupported platform kind: {payload.kind}")
    norm_kind = "profile" if db_kind == "facebook_profile" else "page"

    if db_kind in _NON_FB_KINDS:
        if payload.mode != "repliz":
            raise HTTPException(status_code=400, detail=f"{db_kind} destination must use repliz mode")
        if not payload.repliz_account_id:
            raise HTTPException(status_code=400, detail=f"{db_kind} destination requires repliz_account_id")
        if payload.parent_profile_id is not None:
            raise HTTPException(status_code=400, detail=f"{db_kind} destination cannot have a parent profile")

    try:
        validate_route(
            kind=norm_kind,
            mode=payload.mode,
            parent_profile_id=str(payload.parent_profile_id) if payload.parent_profile_id else None,
            repliz_account_id=payload.repliz_account_id,
            daily_target=payload.daily_target,
            phone_daily_target=payload.phone_daily_target,
            repliz_daily_target=payload.repliz_daily_target,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if db_kind == "facebook_profile":
        if payload.parent_profile_id is not None:
            raise HTTPException(status_code=400, detail="Profile destination cannot have a parent profile")
        if payload.repliz_account_id is not None:
            raise HTTPException(status_code=400, detail="Profile destination cannot have a Repliz account")
    elif db_kind in _NON_FB_KINDS:
        pass
    else:  # facebook_page
        if payload.mode in ("phone", "mixed"):
            if not payload.parent_profile_id:
                raise HTTPException(status_code=400, detail="Page using Phone or Mixed mode requires a parent profile")
            parent = db.query(SocialDestination).filter_by(id=payload.parent_profile_id).first()
            if not parent:
                raise HTTPException(status_code=400, detail="Parent profile destination not found")
            if parent.kind != "facebook_profile":
                raise HTTPException(status_code=400, detail="Parent destination must be a Facebook Profile")
        if payload.mode == "repliz" and payload.parent_profile_id is not None:
            raise HTTPException(status_code=400, detail="Repliz-only page must not have a parent profile")

    # Validate kind vs account type via Repliz if client available
    if payload.repliz_account_id:
        try:
            from app.services.repliz_client import get_repliz_client_from_db, get_repliz_client_from_env
            try:
                client = get_repliz_client_from_db(db)
            except Exception:
                client = get_repliz_client_from_env()
            with client:
                accounts = client.list_accounts() if hasattr(client, "list_accounts") else client.list_facebook_accounts()
                matching_acc = next(
                    (
                        acc for acc in accounts
                        if str(acc.get("_id") or acc.get("id") or "").strip() == str(payload.repliz_account_id).strip()
                    ),
                    None,
                )
                if matching_acc:
                    raw_type = str(matching_acc.get("type") or "facebook").lower()
                    acc_type = "x" if raw_type in ("twitter", "x") else raw_type
                    expected_kind = "facebook_page" if acc_type == "facebook" else acc_type
                    if db_kind != expected_kind:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Account '{payload.repliz_account_id}' is of type '{acc_type}', does not match kind '{db_kind}'",
                        )
        except HTTPException:
            raise
        except Exception:
            pass

    if payload.repliz_account_id:
        existing_rep = (
            db.query(SocialDestination)
            .filter_by(repliz_account_id=payload.repliz_account_id)
            .first()
        )
        if existing_rep:
            raise HTTPException(status_code=400, detail="repliz_account_id already in use")

    if payload.is_active and payload.mode in ("phone", "mixed"):
        raise HTTPException(
            status_code=400,
            detail="Cannot activate phone destination without stable actor ID and verified device assignment",
        )

    dest = SocialDestination(
        content_project_id=payload.content_project_id,
        kind=db_kind,
        name=payload.name.strip(),
        facebook_actor_id=payload.facebook_actor_id.strip() if payload.facebook_actor_id else None,
        parent_profile_id=payload.parent_profile_id,
        repliz_account_id=payload.repliz_account_id.strip() if payload.repliz_account_id else None,
        mode=payload.mode,
        timezone=payload.timezone,
        daily_target=payload.daily_target,
        phone_daily_target=payload.phone_daily_target,
        repliz_daily_target=payload.repliz_daily_target,
        min_gap_minutes=payload.min_gap_minutes,
        supported_media=payload.supported_media,
        is_active=payload.is_active,
    )
    db.add(dest)
    db.commit()
    db.refresh(dest)
    return dest


@router.patch("/destinations/{destination_id}", response_model=SocialDestinationOut)
def update_destination(
    destination_id: int,
    payload: SocialDestinationUpdate,
    db: DB,
    _: CurrentUser,
):
    """Update destination with route validation and nonterminal delivery guards."""
    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")

    fields_set = payload.model_fields_set
    has_kind_update = "kind" in fields_set and payload.kind is not None
    if has_kind_update:
        target_kind = _normalize_kind(payload.kind)
        if target_kind not in _ALL_VALID_KINDS:
            raise HTTPException(status_code=400, detail=f"Unsupported platform kind: {payload.kind}")
        dest_is_fb = dest.kind in _FB_KINDS
        target_is_fb = target_kind in _FB_KINDS
        if dest_is_fb != target_is_fb:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot change channel kind between Facebook and non-Facebook platform ({dest.kind} -> {target_kind})",
            )
        db_kind = target_kind
    else:
        db_kind = dest.kind

    if dest.is_active:
        has_nonterminal = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.destination_id == dest.id,
                PublishDelivery.state.in_(_NONTERMINAL_STATES),
            )
            .first()
        )
        if has_nonterminal:
            if has_kind_update and db_kind != dest.kind:
                raise HTTPException(status_code=400, detail="Cannot change destination kind while nonterminal deliveries exist")
            if "parent_profile_id" in fields_set and payload.parent_profile_id != dest.parent_profile_id:
                raise HTTPException(status_code=400, detail="Cannot change parent profile while nonterminal deliveries exist")
            if "content_project_id" in fields_set and payload.content_project_id != dest.content_project_id:
                raise HTTPException(status_code=400, detail="Cannot change content project while nonterminal deliveries exist")
            if "facebook_actor_id" in fields_set and payload.facebook_actor_id != dest.facebook_actor_id:
                raise HTTPException(status_code=400, detail="Cannot change facebook actor ID while nonterminal deliveries exist")

    content_project_id = payload.content_project_id if "content_project_id" in fields_set and payload.content_project_id is not None else dest.content_project_id
    norm_kind = "profile" if db_kind == "facebook_profile" else "page"
    name = payload.name if "name" in fields_set and payload.name is not None else dest.name
    facebook_actor_id = payload.facebook_actor_id if "facebook_actor_id" in fields_set else dest.facebook_actor_id
    parent_profile_id = payload.parent_profile_id if "parent_profile_id" in fields_set else dest.parent_profile_id
    repliz_account_id = payload.repliz_account_id if "repliz_account_id" in fields_set else dest.repliz_account_id
    mode = payload.mode if "mode" in fields_set and payload.mode is not None else dest.mode
    timezone_val = payload.timezone if "timezone" in fields_set and payload.timezone is not None else dest.timezone
    daily_target = payload.daily_target if "daily_target" in fields_set and payload.daily_target is not None else dest.daily_target

    if mode == "repliz":
        phone_daily_target = payload.phone_daily_target if "phone_daily_target" in fields_set and payload.phone_daily_target is not None else 0
        repliz_daily_target = payload.repliz_daily_target if "repliz_daily_target" in fields_set and payload.repliz_daily_target is not None else daily_target
    elif mode == "phone":
        phone_daily_target = payload.phone_daily_target if "phone_daily_target" in fields_set and payload.phone_daily_target is not None else daily_target
        repliz_daily_target = payload.repliz_daily_target if "repliz_daily_target" in fields_set and payload.repliz_daily_target is not None else 0
    else:
        phone_daily_target = payload.phone_daily_target if "phone_daily_target" in fields_set and payload.phone_daily_target is not None else dest.phone_daily_target
        repliz_daily_target = payload.repliz_daily_target if "repliz_daily_target" in fields_set and payload.repliz_daily_target is not None else dest.repliz_daily_target

    min_gap_minutes = payload.min_gap_minutes if "min_gap_minutes" in fields_set and payload.min_gap_minutes is not None else dest.min_gap_minutes
    supported_media = payload.supported_media if "supported_media" in fields_set and payload.supported_media is not None else dest.supported_media
    is_active = payload.is_active if "is_active" in fields_set and payload.is_active is not None else dest.is_active

    if not db.query(TargetFanpage).filter_by(id=content_project_id).first():
        raise HTTPException(status_code=400, detail="Content project not found")

    try:
        validate_route(
            kind=norm_kind,
            mode=mode,
            parent_profile_id=str(parent_profile_id) if parent_profile_id else None,
            repliz_account_id=repliz_account_id,
            daily_target=daily_target,
            phone_daily_target=phone_daily_target,
            repliz_daily_target=repliz_daily_target,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if db_kind in _NON_FB_KINDS:
        if mode != "repliz":
            raise HTTPException(status_code=400, detail=f"{db_kind} destination must use repliz mode")
        if not repliz_account_id:
            raise HTTPException(status_code=400, detail=f"{db_kind} destination requires repliz_account_id")
        if parent_profile_id is not None:
            raise HTTPException(status_code=400, detail=f"{db_kind} destination cannot have a parent profile")

    if db_kind == "facebook_profile":
        if parent_profile_id is not None:
            raise HTTPException(status_code=400, detail="Profile destination cannot have a parent profile")
        if repliz_account_id is not None:
            raise HTTPException(status_code=400, detail="Profile destination cannot have a Repliz account")
    elif db_kind in _NON_FB_KINDS:
        pass
    else:  # facebook_page
        if parent_profile_id == dest.id:
            raise HTTPException(status_code=400, detail="Page destination cannot be its own parent")
        if mode in ("phone", "mixed"):
            if not parent_profile_id:
                raise HTTPException(status_code=400, detail="Page using Phone or Mixed mode requires a parent profile")
            parent = db.query(SocialDestination).filter_by(id=parent_profile_id).first()
            if not parent:
                raise HTTPException(status_code=400, detail="Parent profile destination not found")
            if parent.kind != "facebook_profile":
                raise HTTPException(status_code=400, detail="Parent destination must be a Facebook Profile")
        if mode == "repliz" and parent_profile_id is not None:
            raise HTTPException(status_code=400, detail="Repliz-only page must not have a parent profile")

    # Validate kind vs account type via Repliz if client available and account/kind was changed
    if repliz_account_id and ("repliz_account_id" in fields_set or has_kind_update):
        try:
            from app.services.repliz_client import get_repliz_client_from_db, get_repliz_client_from_env
            try:
                client = get_repliz_client_from_db(db)
            except Exception:
                client = get_repliz_client_from_env()
            with client:
                accounts = client.list_accounts() if hasattr(client, "list_accounts") else client.list_facebook_accounts()
                matching_acc = next(
                    (
                        acc for acc in accounts
                        if str(acc.get("_id") or acc.get("id") or "").strip() == str(repliz_account_id).strip()
                    ),
                    None,
                )
                if matching_acc:
                    raw_type = str(matching_acc.get("type") or "facebook").lower()
                    acc_type = "x" if raw_type in ("twitter", "x") else raw_type
                    expected_kind = "facebook_page" if acc_type == "facebook" else acc_type
                    if db_kind != expected_kind:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Account '{repliz_account_id}' is of type '{acc_type}', does not match kind '{db_kind}'",
                        )
        except HTTPException:
            raise
        except Exception:
            pass

    if repliz_account_id:
        existing_rep = (
            db.query(SocialDestination)
            .filter(
                SocialDestination.repliz_account_id == repliz_account_id,
                SocialDestination.id != dest.id,
            )
            .first()
        )
        if existing_rep:
            raise HTTPException(status_code=400, detail="repliz_account_id already in use by another destination")

    if is_active and mode in ("phone", "mixed"):
        if not facebook_actor_id or not str(facebook_actor_id).strip():
            raise HTTPException(
                status_code=400,
                detail="Cannot activate phone destination without stable facebook_actor_id",
            )
        parent_actor = None
        if db_kind == "facebook_page":
            parent = db.query(SocialDestination).filter_by(id=parent_profile_id).first()
            if not parent or not parent.facebook_actor_id:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot activate page destination when parent profile has no facebook_actor_id",
                )
            parent_actor = parent.facebook_actor_id

        active_assignments = (
            db.query(DestinationDeviceAssignment)
            .filter_by(destination_id=dest.id, is_active=True)
            .all()
        )
        has_verified = False
        for a in active_assignments:
            if a.verified_at and a.device and a.device.calibration_verified_at and a.device.status == "available":
                if db_kind == "facebook_profile":
                    if a.verified_profile_actor_id == facebook_actor_id:
                        has_verified = True
                        break
                elif db_kind == "facebook_page":
                    if (
                        a.verified_profile_actor_id == parent_actor
                        and a.verified_target_actor_id == facebook_actor_id
                    ):
                        has_verified = True
                        break
        if not has_verified:
            raise HTTPException(
                status_code=400,
                detail="Cannot activate phone route without verified device assignment and calibration",
            )

    dest.content_project_id = content_project_id
    dest.kind = db_kind
    dest.name = name
    dest.facebook_actor_id = facebook_actor_id
    dest.parent_profile_id = parent_profile_id
    dest.repliz_account_id = repliz_account_id
    dest.mode = mode
    dest.timezone = timezone_val
    dest.daily_target = daily_target
    dest.phone_daily_target = phone_daily_target
    dest.repliz_daily_target = repliz_daily_target
    dest.min_gap_minutes = min_gap_minutes
    dest.supported_media = supported_media
    dest.is_active = is_active

    db.commit()
    db.refresh(dest)
    return dest


@router.delete("/destinations/{destination_id}", response_model=SocialDestinationOut)
def delete_destination(destination_id: int, db: DB, _: CurrentUser):
    """Soft deactivate a social destination so delivery history survives."""
    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")
    dest.is_active = False
    db.commit()
    db.refresh(dest)
    return dest


# ── Devices CRUD ──────────────────────────────────────────────────────────────


@router.get("/devices", response_model=list[PhoneDeviceOut])
def list_devices(db: DB, _: CurrentUser):
    """List registered phone devices."""
    return db.query(PhoneDevice).order_by(PhoneDevice.name).all()


@router.get("/devices/discovery", response_model=PhoneDeviceDiscoveryResponse)
def discover_devices(_: CurrentUser):
    """Discover phones from host runner."""
    client = PhoneRunnerClient()
    try:
        return client.discover_devices()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to load phones from the server. You can enter connection details manually.",
        )


@router.get("/devices/{device_id}", response_model=PhoneDeviceOut)
def get_device(device_id: int, db: DB, _: CurrentUser):
    """Get single phone device by ID."""
    device = db.query(PhoneDevice).filter_by(id=device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post(
    "/devices",
    response_model=PhoneDeviceOut,
    status_code=status.HTTP_201_CREATED,
)
def create_device(payload: PhoneDeviceCreate, db: DB, _: CurrentUser):
    """Register a new phone device with strict identifier validation."""
    if payload.adb_serial:
        if db.query(PhoneDevice).filter_by(adb_serial=payload.adb_serial).first():
            raise HTTPException(status_code=400, detail="Device with this adb_serial already exists")
    if payload.tailscale_node_id:
        if db.query(PhoneDevice).filter_by(tailscale_node_id=payload.tailscale_node_id).first():
            raise HTTPException(status_code=400, detail="Device with this tailscale_node_id already exists")

    device = PhoneDevice(
        name=payload.name,
        tailscale_node_id=payload.tailscale_node_id,
        tailscale_ip=payload.tailscale_ip,
        adb_serial=payload.adb_serial,
        expected_profile_actor_id=payload.expected_profile_actor_id,
        expected_profile_name=payload.expected_profile_name,
        screen_width=payload.screen_width,
        screen_height=payload.screen_height,
        screen_density=payload.screen_density,
        screen_rotation=payload.screen_rotation,
        facebook_app_version=payload.facebook_app_version,
        mobilerun_portal_version=payload.mobilerun_portal_version,
        app_card_version=payload.app_card_version,
        status=payload.status,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


@router.patch("/devices/{device_id}", response_model=PhoneDeviceOut)
def update_device(device_id: int, payload: PhoneDeviceUpdate, db: DB, _: CurrentUser):
    """Update phone device attributes and status."""
    device = db.query(PhoneDevice).filter_by(id=device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    if payload.adb_serial and payload.adb_serial != device.adb_serial:
        if db.query(PhoneDevice).filter(PhoneDevice.adb_serial == payload.adb_serial, PhoneDevice.id != device.id).first():
            raise HTTPException(status_code=400, detail="Device with this adb_serial already exists")
    if payload.tailscale_node_id and payload.tailscale_node_id != device.tailscale_node_id:
        if db.query(PhoneDevice).filter(PhoneDevice.tailscale_node_id == payload.tailscale_node_id, PhoneDevice.id != device.id).first():
            raise HTTPException(status_code=400, detail="Device with this tailscale_node_id already exists")

    target_status = payload.status if ("status" in payload.model_fields_set and payload.status is not None) else device.status
    target_serial = payload.adb_serial if "adb_serial" in payload.model_fields_set else device.adb_serial

    if target_status == "available" and (not target_serial or not str(target_serial).strip()):
        raise HTTPException(
            status_code=400,
            detail="adb_serial is required before device status can be available",
        )

    for field, val in payload.model_dump(exclude_unset=True).items():
        setattr(device, field, val)

    db.commit()
    db.refresh(device)
    return device


def _verify_runner_token(token: Optional[str]) -> bool:
    if not token or not token.strip():
        return False
    expected = (
        os.environ.get("PHONE_RUNNER_TOKEN", "").strip()
        or getattr(get_settings(), "phone_runner_token", "").strip()
    )
    if not expected:
        return False
    return hmac.compare_digest(token.strip().encode("utf-8"), expected.encode("utf-8"))


@router.post("/devices/{device_id}/dry-run")
def run_device_dry_run(
    device_id: int,
    payload: DeviceDryRunRequest,
    db: DB,
    _: CurrentUser,
):
    """Trigger dry-run on host runner for device, verifying calibration and actor identity.

    Safety invariants:
    - Atomically acquires DeviceLease (delivery_id=None) before calling host runner.
    - Rejects active live leases with 409 Conflict.
    - Releases only its acquired lease in finally block.
    - Requires matching expected main Profile ID and target actor.
    - Requires stopped_before_publish=True and all runner stages passed.
    - Requires valid observed resolution, App Card, Facebook, and Portal versions.
    - Updates device geometry/versions and calibration/assignment only after validating all evidence.
    - Failures fail closed and leave device and assignment completely unchanged.
    """
    device = db.query(PhoneDevice).filter_by(id=device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if not device.adb_serial:
        raise HTTPException(status_code=400, detail="Device missing adb_serial")

    dest = None
    assignment = None
    if payload.destination_id is not None:
        dest = db.query(SocialDestination).filter_by(id=payload.destination_id).first()
        if not dest:
            raise HTTPException(status_code=400, detail="Destination not found")
        assignment = (
            db.query(DestinationDeviceAssignment)
            .filter_by(destination_id=dest.id, device_id=device.id)
            .first()
        )
        if not assignment:
            raise HTTPException(status_code=400, detail="Device is not assigned to this destination")

    if dest:
        if dest.kind == "facebook_page":
            if not dest.parent_profile_id:
                raise HTTPException(status_code=400, detail="Page destination missing parent_profile_id")
            parent = dest.parent_profile
            if parent is None:
                parent = db.query(SocialDestination).filter_by(id=dest.parent_profile_id).first()
            if not parent:
                raise HTTPException(status_code=400, detail="Parent profile destination not found")
            if parent.kind != "facebook_profile":
                raise HTTPException(status_code=400, detail="Parent destination kind must be facebook_profile")
            if not parent.facebook_actor_id or not str(parent.facebook_actor_id).strip():
                raise HTTPException(status_code=400, detail="Parent profile missing or has no facebook_actor_id")
            if not dest.facebook_actor_id or not str(dest.facebook_actor_id).strip():
                raise HTTPException(status_code=400, detail="Page destination has no facebook_actor_id")
            expected_root_actor_id = str(parent.facebook_actor_id).strip()
            expected_target_actor_id = str(dest.facebook_actor_id).strip()
            main_profile = {"id": expected_root_actor_id, "name": parent.name}
            target_page = {"id": expected_target_actor_id, "name": dest.name}
        elif dest.kind == "facebook_profile":
            if not dest.facebook_actor_id or not str(dest.facebook_actor_id).strip():
                raise HTTPException(status_code=400, detail="Profile destination has no facebook_actor_id")
            expected_root_actor_id = str(dest.facebook_actor_id).strip()
            expected_target_actor_id = str(dest.facebook_actor_id).strip()
            main_profile = {"id": expected_root_actor_id, "name": dest.name}
            target_page = None
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported destination kind: {dest.kind}")
    else:
        if not device.expected_profile_actor_id or not str(device.expected_profile_actor_id).strip():
            raise HTTPException(status_code=400, detail="Device has no expected_profile_actor_id configured")
        expected_root_actor_id = str(device.expected_profile_actor_id).strip()
        expected_target_actor_id = str(device.expected_profile_actor_id).strip()
        main_profile = {
            "id": expected_root_actor_id,
            "name": device.expected_profile_name or device.name,
        }
        target_page = None

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # 1. Atomic exclusive device lease check & acquisition before calling runner
    existing_lease = db.query(DeviceLease).filter_by(device_id=device.id).first()
    if existing_lease:
        if existing_lease.expires_at > now:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Device {device.id} is currently leased by an active task (expires at {existing_lease.expires_at.isoformat()})",
            )
        # Reclaim expired lease
        existing_lease.delivery_id = None
        existing_lease.acquired_at = now
        existing_lease.expires_at = now + timedelta(minutes=5)
    else:
        calibration_lease = DeviceLease(
            device_id=device.id,
            delivery_id=None,
            acquired_at=now,
            expires_at=now + timedelta(minutes=5),
        )
        db.add(calibration_lease)

    db.commit()
    lease_acquired = True

    try:
        client = PhoneRunnerClient()
        res = client.dry_run(device=device, main_profile=main_profile, target_page=target_page)

        if not res.get("success"):
            err_detail = res.get("detail") or res.get("status") or "unknown_runner_error"
            raise HTTPException(
                status_code=400,
                detail=f"Dry-run failed on host runner: {err_detail}",
            )

        evidence = res.get("evidence")
        if not isinstance(evidence, dict):
            raise HTTPException(status_code=500, detail="Dry-run safety violation: invalid evidence object")
        if evidence.get("stopped_before_publish") is not True:
            raise HTTPException(status_code=500, detail="Dry-run safety violation: stopped_before_publish not proven")

        # 2. Require all runner stages to have passed with boolean True
        stages = res.get("stages")
        if not isinstance(stages, list) or not stages or any(s.get("passed") is not True for s in stages):
            failed_stage = next((s.get("stage", "stage") for s in (stages or []) if s.get("passed") is not True), "stage")
            raise HTTPException(status_code=400, detail=f"Dry-run stage '{failed_stage}' failed")

        # 3. Require valid observed resolution and versions
        resolution = evidence.get("resolution")
        if not resolution:
            raise HTTPException(status_code=400, detail="Dry-run missing observed resolution evidence")

        m_res = re.match(r"^(\d+)[xX](\d+)$", str(resolution).strip())
        if not m_res:
            raise HTTPException(status_code=400, detail=f"Invalid observed resolution format: {resolution}")
        observed_w, observed_h = int(m_res.group(1)), int(m_res.group(2))

        # If device already had pre-existing geometry, mismatch must fail
        if device.screen_width and device.screen_height:
            if device.screen_width != observed_w or device.screen_height != observed_h:
                raise HTTPException(
                    status_code=400,
                    detail=f"Device geometry mismatch: expected {device.screen_width}x{device.screen_height}, observed {observed_w}x{observed_h}",
                )

        app_card_ver = str(evidence.get("app_card_version") or "").strip()
        facebook_ver = str(evidence.get("facebook_version") or "").strip()
        portal_ver = str(evidence.get("portal_version") or evidence.get("mobilerun_portal_version") or "").strip()
        if not app_card_ver or not facebook_ver or not portal_ver:
            raise HTTPException(
                status_code=400,
                detail="Dry-run evidence missing required App Card, Facebook, or Portal version",
            )

        # 4. Require matching expected main Profile ID and active composer actor ID
        verified_main_profile_id = str(evidence.get("verified_main_profile_id") or "").strip()
        active_composer_actor_id = str(evidence.get("active_composer_actor_id") or "").strip()

        if not verified_main_profile_id:
            raise HTTPException(status_code=400, detail="Dry-run evidence missing verified_main_profile_id")
        if not active_composer_actor_id:
            raise HTTPException(status_code=400, detail="Dry-run evidence missing active_composer_actor_id")

        if verified_main_profile_id != expected_root_actor_id:
            raise HTTPException(
                status_code=400,
                detail=f"Main profile mismatch: expected root actor '{expected_root_actor_id}', found '{verified_main_profile_id}'",
            )

        if active_composer_actor_id != expected_target_actor_id:
            raise HTTPException(
                status_code=400,
                detail=f"Target actor mismatch: expected actor '{expected_target_actor_id}', found '{active_composer_actor_id}'",
            )

        # 5. ALL evidence validated — record observed calibration fields
        # ponytail: updates calibration and dimensions without modifying device.expected_profile_actor_id default
        device.screen_width = observed_w
        device.screen_height = observed_h
        device.app_card_version = app_card_ver
        device.facebook_app_version = facebook_ver
        device.mobilerun_portal_version = portal_ver
        device.calibration_verified_at = now
        device.last_seen_at = now

        if assignment and dest:
            _apply_assignment_verification(
                db=db,
                assignment=assignment,
                dest=dest,
                device=device,
                verified_profile_actor_id=verified_main_profile_id,
                verified_target_actor_id=active_composer_actor_id,
            )

        db.commit()
        db.refresh(device)
        return {
            "status": "success",
            "device_id": device.id,
            "calibration_verified_at": device.calibration_verified_at,
            "assignment_verified": bool(assignment and assignment.verified_at),
            "result": res,
        }
    finally:
        if lease_acquired:
            # Release only the calibration lease we acquired
            db.query(DeviceLease).filter(
                DeviceLease.device_id == device.id,
                DeviceLease.delivery_id.is_(None),
            ).delete(synchronize_session=False)
            db.commit()


# ── Assignments CRUD ──────────────────────────────────────────────────────────


@router.get("/assignments", response_model=list[DestinationDeviceAssignmentOut])
def list_assignments(
    db: DB,
    _: CurrentUser,
    destination_id: Optional[int] = Query(None),
    device_id: Optional[int] = Query(None),
    is_active: Optional[bool] = Query(None),
):
    """List destination device assignments with optional filters."""
    q = db.query(DestinationDeviceAssignment)
    if destination_id is not None:
        q = q.filter(DestinationDeviceAssignment.destination_id == destination_id)
    if device_id is not None:
        q = q.filter(DestinationDeviceAssignment.device_id == device_id)
    if is_active is not None:
        q = q.filter(DestinationDeviceAssignment.is_active == is_active)
    return q.order_by(DestinationDeviceAssignment.priority).all()


def _is_active_verified_assignment(
    a: DestinationDeviceAssignment,
    dest: SocialDestination,
    db: Session,
) -> bool:
    if not (a.is_active and a.verified_at and a.device):
        return False
    if not (a.device.calibration_verified_at and a.device.status == "available"):
        return False
    if dest.kind == "facebook_profile":
        return (
            bool(dest.facebook_actor_id)
            and a.verified_profile_actor_id == dest.facebook_actor_id
            and a.verified_target_actor_id == dest.facebook_actor_id
        )
    elif dest.kind == "facebook_page":
        parent = dest.parent_profile
        if parent is None and dest.parent_profile_id:
            parent = db.query(SocialDestination).filter_by(id=dest.parent_profile_id).first()
        parent_actor = parent.facebook_actor_id if (parent and parent.kind == "facebook_profile") else None
        return (
            bool(parent_actor)
            and bool(dest.facebook_actor_id)
            and a.verified_profile_actor_id == parent_actor
            and a.verified_target_actor_id == dest.facebook_actor_id
        )
    return False


def _guard_last_active_verified_assignment(
    assignment: DestinationDeviceAssignment,
    dest: SocialDestination | None,
    db: Session,
) -> None:
    if (
        dest
        and dest.is_active
        and dest.mode in ("phone", "mixed")
        and assignment.is_active
        and _is_active_verified_assignment(assignment, dest, db)
    ):
        other_assignments = (
            db.query(DestinationDeviceAssignment)
            .filter(
                DestinationDeviceAssignment.destination_id == dest.id,
                DestinationDeviceAssignment.id != assignment.id,
                DestinationDeviceAssignment.is_active.is_(True),
            )
            .all()
        )
        has_other_verified = any(
            _is_active_verified_assignment(other, dest, db)
            for other in other_assignments
        )
        if not has_other_verified:
            raise HTTPException(
                status_code=409,
                detail="Cannot deactivate last active verified device assignment for active Phone or Mixed destination. Deactivate destination first or assign another verified device.",
            )


@router.post("/assignments", response_model=DestinationDeviceAssignmentOut)
def upsert_assignment(
    payload: DestinationDeviceAssignmentCreate,
    db: DB,
    _: CurrentUser,
    x_dry_run_callback: Optional[str] = Header(None),
):
    """Upsert destination device assignment. Browser creates unverified assignment; dry-run callback may record verification."""
    dest = db.query(SocialDestination).filter_by(id=payload.destination_id).first()
    if not dest:
        raise HTTPException(status_code=400, detail="Destination not found")
    device = db.query(PhoneDevice).filter_by(id=payload.device_id).first()
    if not device:
        raise HTTPException(status_code=400, detail="Device not found")

    existing = (
        db.query(DestinationDeviceAssignment)
        .filter_by(destination_id=payload.destination_id, device_id=payload.device_id)
        .first()
    )

    is_trusted_callback = False
    if x_dry_run_callback is not None:
        if not _verify_runner_token(x_dry_run_callback):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid runner token for assignment verification callback",
            )
        is_trusted_callback = True

    if existing:
        if payload.is_active:
            collision = (
                db.query(DestinationDeviceAssignment)
                .filter(
                    DestinationDeviceAssignment.destination_id == payload.destination_id,
                    DestinationDeviceAssignment.priority == payload.priority,
                    DestinationDeviceAssignment.is_active == True,
                    DestinationDeviceAssignment.id != existing.id,
                )
                .first()
            )
            if collision:
                raise HTTPException(
                    status_code=400,
                    detail=f"Priority {payload.priority} already assigned to another device for this destination",
                )
        elif existing.is_active:
            _guard_last_active_verified_assignment(existing, dest, db)

        existing.priority = payload.priority
        existing.is_active = payload.is_active

        if is_trusted_callback and payload.verified_profile_actor_id:
            _apply_assignment_verification(
                db=db,
                assignment=existing,
                dest=dest,
                device=device,
                verified_profile_actor_id=payload.verified_profile_actor_id,
                verified_target_actor_id=payload.verified_target_actor_id,
            )

        db.commit()
        db.refresh(existing)
        return existing

    if payload.is_active:
        collision = (
            db.query(DestinationDeviceAssignment)
            .filter(
                DestinationDeviceAssignment.destination_id == payload.destination_id,
                DestinationDeviceAssignment.priority == payload.priority,
                DestinationDeviceAssignment.is_active == True,
            )
            .first()
        )
        if collision:
            raise HTTPException(
                status_code=400,
                detail=f"Priority {payload.priority} already assigned to another device for this destination",
            )

    assignment = DestinationDeviceAssignment(
        destination_id=payload.destination_id,
        device_id=payload.device_id,
        priority=payload.priority,
        is_active=payload.is_active,
    )

    if is_trusted_callback and payload.verified_profile_actor_id:
        _apply_assignment_verification(
            db=db,
            assignment=assignment,
            dest=dest,
            device=device,
            verified_profile_actor_id=payload.verified_profile_actor_id,
            verified_target_actor_id=payload.verified_target_actor_id,
        )

    db.add(assignment)
    db.commit()
    db.refresh(assignment)
    return assignment


@router.delete(
    "/assignments/{destination_id}/{device_id}",
    response_model=DestinationDeviceAssignmentOut,
)
def delete_assignment(destination_id: int, device_id: int, db: DB, _: CurrentUser):
    """Soft deactivate a device assignment."""
    assignment = (
        db.query(DestinationDeviceAssignment)
        .filter_by(destination_id=destination_id, device_id=device_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")

    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    _guard_last_active_verified_assignment(assignment, dest, db)

    assignment.is_active = False
    db.commit()
    db.refresh(assignment)
    return assignment


@router.post("/assignments/verify", response_model=DestinationDeviceAssignmentOut)
def verify_assignment(
    payload: DestinationDeviceAssignmentVerify,
    db: DB,
    _: CurrentUser,
    x_dry_run_callback: Optional[str] = Header(None),
):
    """Record verification for a destination device assignment from an authenticated dry-run callback."""
    if not _verify_runner_token(x_dry_run_callback):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing runner token for assignment verification",
        )
    assignment = (
        db.query(DestinationDeviceAssignment)
        .filter_by(destination_id=payload.destination_id, device_id=payload.device_id)
        .first()
    )
    if not assignment:
        raise HTTPException(status_code=404, detail="Assignment not found")
    dest = db.query(SocialDestination).filter_by(id=payload.destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")
    device = db.query(PhoneDevice).filter_by(id=payload.device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    _apply_assignment_verification(
        db=db,
        assignment=assignment,
        dest=dest,
        device=device,
        verified_profile_actor_id=payload.verified_profile_actor_id,
        verified_target_actor_id=payload.verified_target_actor_id,
    )
    db.commit()
    db.refresh(assignment)
    return assignment


def _apply_assignment_verification(
    db: DB,
    assignment: DestinationDeviceAssignment,
    dest: SocialDestination,
    device: PhoneDevice,
    verified_profile_actor_id: str,
    verified_target_actor_id: Optional[str],
):
    """Verify actor hierarchy and apply verification timestamp to assignment."""
    if not device.calibration_verified_at:
        raise HTTPException(
            status_code=400,
            detail="Device calibration must be verified before verifying assignment",
        )

    clean_profile_actor = str(verified_profile_actor_id).strip() if verified_profile_actor_id else ""
    clean_target_actor = str(verified_target_actor_id).strip() if verified_target_actor_id else ""

    if not clean_profile_actor:
        raise HTTPException(
            status_code=400,
            detail="verified_profile_actor_id is required",
        )
    if not clean_target_actor:
        raise HTTPException(
            status_code=400,
            detail="verified_target_actor_id is required",
        )

    if dest.kind == "facebook_profile":
        if not dest.facebook_actor_id or clean_profile_actor != dest.facebook_actor_id:
            raise HTTPException(
                status_code=400,
                detail=f"verified_profile_actor_id ({clean_profile_actor}) must match Profile actor ID ({dest.facebook_actor_id})",
            )
        if clean_target_actor != dest.facebook_actor_id:
            raise HTTPException(
                status_code=400,
                detail=f"verified_target_actor_id ({clean_target_actor}) must match Profile actor ID ({dest.facebook_actor_id})",
            )
        assignment.verified_profile_actor_id = clean_profile_actor
        assignment.verified_target_actor_id = clean_target_actor
    elif dest.kind == "facebook_page":
        if not dest.parent_profile_id:
            raise HTTPException(
                status_code=400,
                detail="Page Phone route requires destination to have parent profile",
            )
        parent = dest.parent_profile
        if parent is None:
            parent = db.query(SocialDestination).filter_by(id=dest.parent_profile_id).first()
        if not parent or not parent.facebook_actor_id:
            raise HTTPException(
                status_code=400,
                detail="Parent profile destination or parent actor ID missing",
            )
        if parent.kind != "facebook_profile":
            raise HTTPException(
                status_code=400,
                detail="Parent destination kind must be facebook_profile",
            )
        if clean_profile_actor != parent.facebook_actor_id:
            raise HTTPException(
                status_code=400,
                detail=f"verified_profile_actor_id ({clean_profile_actor}) must match parent profile actor ID ({parent.facebook_actor_id})",
            )
        if not dest.facebook_actor_id or clean_target_actor != dest.facebook_actor_id:
            raise HTTPException(
                status_code=400,
                detail=f"verified_target_actor_id ({clean_target_actor}) must match Page actor ID ({dest.facebook_actor_id})",
            )
        assignment.verified_profile_actor_id = clean_profile_actor
        assignment.verified_target_actor_id = clean_target_actor
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported destination kind: {dest.kind}")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assignment.verified_at = now
    dest.identity_verified_at = now


# ── Activity ─────────────────────────────────────────────────────────────────


@router.get("/activity", response_model=list[DeliveryActivityOut])
def list_activity(
    db: DB,
    _: CurrentUser,
    content_project_id: Optional[int] = Query(None),
    destination_id: Optional[int] = Query(None),
    device_id: Optional[int] = Query(None),
    state: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List delivery activities with state, method, IDs/links, and events."""
    q = (
        db.query(PublishDelivery)
        .options(
            joinedload(PublishDelivery.destination),
            joinedload(PublishDelivery.device),
            selectinload(PublishDelivery.events),
        )
    )
    if destination_id is not None:
        q = q.filter(PublishDelivery.destination_id == destination_id)
    if device_id is not None:
        q = q.filter(PublishDelivery.device_id == device_id)
    if state is not None:
        q = q.filter(PublishDelivery.state == state)
    if content_project_id is not None:
        q = q.join(SocialDestination).filter(SocialDestination.content_project_id == content_project_id)

    deliveries = (
        q.order_by(PublishDelivery.created_at.desc(), PublishDelivery.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for d in deliveries:
        dest = d.destination
        dev = d.device
        results.append(
            DeliveryActivityOut(
                id=d.id,
                publish_job_id=d.publish_job_id,
                destination_id=d.destination_id,
                destination_name=dest.name if dest else None,
                destination_kind=dest.kind if dest else None,
                content_project_id=dest.content_project_id if dest else None,
                device_id=d.device_id,
                device_name=dev.name if dev else None,
                state=d.state,
                intended_method=d.intended_method,
                actual_method=d.actual_method,
                scheduled_for=d.scheduled_for,
                claimed_at=d.claimed_at,
                attempt_count=d.attempt_count or 0,
                caption_snapshot=d.caption_snapshot,
                media_kind=d.media_kind,
                title_snapshot=d.title_snapshot,
                repliz_schedule_id=d.repliz_schedule_id,
                facebook_post_id=d.facebook_post_id,
                facebook_permalink=d.facebook_permalink,
                uncertain_since=d.uncertain_since,
                last_error=d.last_error,
                published_at=d.published_at,
                created_at=d.created_at,
                updated_at=d.updated_at,
                events=d.events or [],
            )
        )
    return results


# ── Delivery Copies & Manual Actions (TASK C1) ────────────────────────────────


class DeliveryCopyOut(BaseModel):
    id: int
    publish_job_id: int
    destination_id: int
    destination_name: Optional[str] = None
    destination_kind: Optional[str] = None
    platform: Optional[str] = None
    kind: Optional[str] = None
    method: Optional[str] = None
    intended_method: Optional[str] = None
    actual_method: Optional[str] = None
    state: str
    repliz_schedule_id: Optional[str] = None
    facebook_post_id: Optional[str] = None
    facebook_permalink: Optional[str] = None
    scheduled_for: Optional[datetime] = None
    published_at: Optional[datetime] = None
    last_error: Optional[str] = None
    uncertain_since: Optional[datetime] = None
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    attempt_count: int = 0

    model_config = {"from_attributes": True}


def _serialize_delivery_copy(d: PublishDelivery) -> DeliveryCopyOut:
    dest = d.destination
    dev = d.device
    method = d.actual_method or d.intended_method
    platform_kind = dest.kind if dest else None
    return DeliveryCopyOut(
        id=d.id,
        publish_job_id=d.publish_job_id,
        destination_id=d.destination_id,
        destination_name=dest.name if dest else None,
        destination_kind=platform_kind,
        platform=platform_kind,
        kind=platform_kind,
        method=method,
        intended_method=d.intended_method,
        actual_method=d.actual_method,
        state=d.state,
        repliz_schedule_id=d.repliz_schedule_id,
        facebook_post_id=d.facebook_post_id,
        facebook_permalink=d.facebook_permalink,
        scheduled_for=d.scheduled_for,
        published_at=d.published_at,
        last_error=d.last_error,
        uncertain_since=d.uncertain_since,
        device_id=d.device_id,
        device_name=dev.name if dev else None,
        attempt_count=d.attempt_count or 0,
    )


@router.get("/deliveries", response_model=list[DeliveryCopyOut])
def get_deliveries(
    db: DB,
    _: CurrentUser,
    publish_job_ids: Optional[str] = Query(None, description="Comma-separated publish job IDs (max 100)"),
):
    """Fetch channel copies for specified publish jobs (max 100)."""
    if not publish_job_ids:
        return []

    job_ids: list[int] = []
    for part in publish_job_ids.split(","):
        s = part.strip()
        if not s:
            continue
        try:
            job_ids.append(int(s))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid job ID: '{s}'")

    if not job_ids:
        return []

    if len(job_ids) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 job IDs allowed per request")

    deliveries = (
        db.query(PublishDelivery)
        .options(
            joinedload(PublishDelivery.destination),
            joinedload(PublishDelivery.device),
        )
        .filter(PublishDelivery.publish_job_id.in_(job_ids))
        .order_by(PublishDelivery.publish_job_id.asc(), PublishDelivery.id.asc())
        .all()
    )

    return [_serialize_delivery_copy(d) for d in deliveries]


@router.post("/deliveries/{id}/mark-posted", response_model=DeliveryCopyOut)
def mark_delivery_posted(
    id: int,
    db: DB,
    _: CurrentUser,
):
    """Mark an uncertain delivery copy as published by hand."""
    delivery = (
        db.query(PublishDelivery)
        .options(
            joinedload(PublishDelivery.destination),
            joinedload(PublishDelivery.device),
        )
        .filter(PublishDelivery.id == id)
        .populate_existing()
        .with_for_update(of=PublishDelivery)
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery copy not found")

    if delivery.state != "uncertain":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot mark posted from state '{delivery.state}'; only 'uncertain' deliveries can be marked posted",
        )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    prev_state = delivery.state
    delivery.state = "published"
    delivery.published_at = delivery.published_at or now
    delivery.uncertain_since = None

    event = DeliveryEvent(
        delivery_id=delivery.id,
        event_type="manual_override",
        details={
            "actor": "owner",
            "note": "Marked posted by hand",
            "previous_state": prev_state,
            "new_state": "published",
        },
    )
    db.add(event)

    all_deliveries = (
        db.query(PublishDelivery)
        .filter(PublishDelivery.publish_job_id == delivery.publish_job_id)
        .all()
    )
    all_accepted = bool(all_deliveries) and all(
        d.state in ("repliz_scheduled", "published") for d in all_deliveries
    )
    if all_accepted:
        job = db.query(PublishJob).filter(PublishJob.id == delivery.publish_job_id).first()
        if job and job.status != PublishJobStatus.published:
            job.status = PublishJobStatus.published
            job.published_at = delivery.published_at

    db.commit()
    db.refresh(delivery)
    return _serialize_delivery_copy(delivery)


@router.post("/deliveries/{id}/mark-not-posted", response_model=DeliveryCopyOut)
def mark_delivery_not_posted(
    id: int,
    db: DB,
    _: CurrentUser,
):
    """Mark an uncertain delivery copy as not posted (failed) by hand."""
    delivery = (
        db.query(PublishDelivery)
        .options(
            joinedload(PublishDelivery.destination),
            joinedload(PublishDelivery.device),
        )
        .filter(PublishDelivery.id == id)
        .populate_existing()
        .with_for_update(of=PublishDelivery)
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery copy not found")

    if delivery.state != "uncertain":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot mark not posted from state '{delivery.state}'; only 'uncertain' deliveries can be marked not posted",
        )

    prev_state = delivery.state
    delivery.state = "failed"
    delivery.last_error = "Marked not posted by hand"
    delivery.uncertain_since = None

    event = DeliveryEvent(
        delivery_id=delivery.id,
        event_type="manual_override",
        details={
            "actor": "owner",
            "note": "Marked not posted by hand",
            "previous_state": prev_state,
            "new_state": "failed",
        },
    )
    db.add(event)

    job = db.query(PublishJob).filter(PublishJob.id == delivery.publish_job_id).first()
    if job and job.status == PublishJobStatus.publishing:
        job.status = PublishJobStatus.pending_publish

    db.commit()
    db.refresh(delivery)
    return _serialize_delivery_copy(delivery)


@router.post("/deliveries/{id}/cancel", response_model=DeliveryCopyOut)
def cancel_scheduled_delivery(
    id: int,
    db: DB,
    _: CurrentUser,
):
    """Cancel a scheduled delivery copy before it starts posting."""
    delivery = (
        db.query(PublishDelivery)
        .options(
            joinedload(PublishDelivery.destination),
            joinedload(PublishDelivery.device),
        )
        .filter(PublishDelivery.id == id)
        .populate_existing()
        .with_for_update(of=PublishDelivery)
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery copy not found")

    if delivery.state not in ("scheduled", "waiting_for_slot", "waiting_for_device"):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot cancel a delivery in state '{delivery.state}'",
        )

    if delivery.claimed_at is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel a delivery that is already claimed",
        )

    if delivery.facebook_post_id is not None or delivery.repliz_schedule_id is not None:
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel a delivery that already has an external post ID or schedule ID",
        )

    prev_state = delivery.state
    delivery.state = "skipped"
    delivery.last_error = "Cancelled by hand before posting"

    event = DeliveryEvent(
        delivery_id=delivery.id,
        event_type="manual_override",
        details={
            "actor": "owner",
            "note": "Cancelled before posting",
            "previous_state": prev_state,
            "new_state": "skipped",
        },
    )
    db.add(event)

    all_deliveries = (
        db.query(PublishDelivery)
        .filter(PublishDelivery.publish_job_id == delivery.publish_job_id)
        .all()
    )
    active_states = {
        "scheduled",
        "waiting_for_slot",
        "waiting_for_device",
        "publishing",
        "uncertain",
        "needs_human",
        "repliz_scheduled",
    }
    has_active = any(d.state in active_states for d in all_deliveries)
    has_published = any(d.state == "published" for d in all_deliveries)
    if not has_active and not has_published:
        job = db.query(PublishJob).filter(PublishJob.id == delivery.publish_job_id).first()
        if job:
            job.status = PublishJobStatus.skipped

    db.commit()
    db.refresh(delivery)
    return _serialize_delivery_copy(delivery)


@router.post("/deliveries/{id}/retry", response_model=DeliveryCopyOut)
def retry_delivery(
    id: int,
    db: DB,
    _: CurrentUser,
):
    """Retry a failed delivery copy that has no external ID."""
    deliv_stub = (
        db.query(PublishDelivery.id, PublishDelivery.destination_id)
        .filter(PublishDelivery.id == id)
        .first()
    )
    if not deliv_stub:
        raise HTTPException(status_code=404, detail="Delivery copy not found")

    dest = (
        db.query(SocialDestination)
        .filter(SocialDestination.id == deliv_stub.destination_id)
        .populate_existing()
        .with_for_update()
        .first()
    )
    if not dest:
        raise HTTPException(status_code=404, detail="Social destination not found")

    delivery = (
        db.query(PublishDelivery)
        .options(
            joinedload(PublishDelivery.destination),
            joinedload(PublishDelivery.device),
        )
        .filter(PublishDelivery.id == id)
        .populate_existing()
        .with_for_update(of=PublishDelivery)
        .first()
    )
    if not delivery:
        raise HTTPException(status_code=404, detail="Delivery copy not found")

    if delivery.state != "failed":
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry delivery in state '{delivery.state}'; only 'failed' copies can be retried",
        )

    if delivery.repliz_schedule_id or delivery.facebook_post_id:
        ext_id = delivery.repliz_schedule_id or delivery.facebook_post_id
        raise HTTPException(
            status_code=409,
            detail=f"Cannot retry copy with external ID '{ext_id}'",
        )

    fanpage = getattr(dest, "content_project", None)
    if fanpage is None and dest.content_project_id:
        fanpage = db.query(TargetFanpage).filter_by(id=dest.content_project_id).first()

    sleep_start = None
    sleep_end = None
    if (
        fanpage
        and getattr(fanpage, "publish_sleep_start_hour", None) is not None
        and getattr(fanpage, "publish_sleep_end_hour", None) is not None
    ):
        sleep_start = fanpage.publish_sleep_start_hour
        sleep_end = fanpage.publish_sleep_end_hour

    dest_hour_weights = getattr(fanpage, "publish_hour_weights", None) if fanpage else None

    aware_now = datetime.now(timezone.utc)
    existing_slots_utc = get_destination_reservations_utc(db, dest)

    next_slot_utc = compute_audience_slot(
        now_utc=aware_now,
        timezone_name=dest.timezone,
        daily_target=dest.daily_target,
        min_gap_minutes=dest.min_gap_minutes,
        existing_slots_utc=existing_slots_utc,
        sleep_start_hour=sleep_start,
        sleep_end_hour=sleep_end,
        namespace=f"dest-{dest.id}",
        hour_weights=dest_hour_weights,
    )
    naive_scheduled_for = next_slot_utc.astimezone(timezone.utc).replace(tzinfo=None)

    target_tz = zoneinfo.ZoneInfo(dest.timezone)
    slot_local_date = next_slot_utc.astimezone(target_tz).date()
    existing_on_day = sum(
        1
        for s in existing_slots_utc
        if s.astimezone(target_tz).date() == slot_local_date
    )

    intended_method = choose_method_for_slot(
        mode=dest.mode,
        slot_index=existing_on_day,
        phone_daily_target=dest.phone_daily_target,
        repliz_daily_target=dest.repliz_daily_target,
    )

    supported = dest.supported_media or []
    if isinstance(supported, str):
        try:
            supported = json.loads(supported)
        except Exception:
            supported = [supported]
    is_supported = (delivery.media_kind in supported) if (delivery.media_kind and supported) else True

    if not is_supported:
        new_state = "waiting_for_slot"
        assigned_device_id = None
        new_last_error = f"Unsupported media kind '{delivery.media_kind}' for destination"
    elif intended_method == "phone":
        assigned_device_id = None
        has_available = False
        assignments = (
            db.query(DestinationDeviceAssignment)
            .filter(
                DestinationDeviceAssignment.destination_id == dest.id,
                DestinationDeviceAssignment.is_active.is_(True),
            )
            .order_by(DestinationDeviceAssignment.priority)
            .all()
        )
        for a in assignments:
            if a.device and a.device.status == "available":
                has_available = True
                assigned_device_id = a.device.id
                break

        if has_available:
            new_state = "scheduled"
            new_last_error = None
        else:
            new_state = "waiting_for_device"
            new_last_error = "Device not available"
    else:  # repliz
        new_state = "scheduled"
        assigned_device_id = None
        new_last_error = None

    prev_state = delivery.state
    delivery.state = new_state
    delivery.device_id = assigned_device_id
    delivery.intended_method = intended_method
    delivery.actual_method = None
    delivery.scheduled_for = naive_scheduled_for
    delivery.claimed_at = None
    delivery.claim_token = None
    delivery.attempt_count = 0
    delivery.last_error = new_last_error
    delivery.uncertain_since = None
    delivery.published_at = None

    event = DeliveryEvent(
        delivery_id=delivery.id,
        event_type="manual_override",
        details={
            "actor": "owner",
            "note": "Retried by hand",
            "previous_state": prev_state,
            "new_state": new_state,
            "intended_method": intended_method,
            "scheduled_for": naive_scheduled_for.isoformat() if naive_scheduled_for else None,
            "reason": new_last_error,
        },
    )
    db.add(event)

    job = db.query(PublishJob).filter(PublishJob.id == delivery.publish_job_id).first()
    if job and job.status == PublishJobStatus.failed:
        job.status = PublishJobStatus.pending_publish

    db.commit()
    db.refresh(delivery)

    try:
        from app.tasks.social_delivery import dispatch_social_deliveries
        dispatch_social_deliveries.delay()
    except Exception as exc:
        logger.warning("Could not enqueue dispatch_social_deliveries: %s", exc)

    return _serialize_delivery_copy(delivery)

