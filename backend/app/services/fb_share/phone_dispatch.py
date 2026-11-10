"""Phone (phone_auto) group posting: due-task selection, FB app version gate, device lease, outcome mapping,
and calibration rehearsals. Contract: .notes/CONTRACT-group-publish.md.

The tick only selects due tasks (`dispatch_phone_due`); the post itself runs in `publish_task` on the single-slot
`delivery` worker, under a DeviceLease (delivery_id NULL) so it never overlaps a Page phone delivery.
"""

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.models.fb_group_share import (
    FbGroup, FbGroupMembership, FbShareBrandSettings, FbShareDelivery, FbShareItem, FbShareTask,
)
from app.models.social_publishing import DeviceLease, PhoneDevice, SocialDestination
from . import caps
from .brand_settings import get_brand_share
from .group_captions import get_task_caption

logger = logging.getLogger(__name__)

LEASE_TTL_MINUTES = 30            # runner budget is 600 s; the lease outlives one call with margin
STALE_PUBLISHING_MINUTES = 45     # a claim older than this was abandoned by a dead worker -> uncertain
VERSION_CACHE_TTL_S = 600         # contract: /probe fb_version cached at most 10 min
RETRY_DELAYS_MIN = (15, 30, 60)   # failed -> retry 15/30/60 min, then failed
BLOCK_PAUSE_HOURS = 72
MAX_CAPTION_CHARS = 2000
MAX_MEDIA = 10
# States where the phone touched the composer: they advance the per-account minimum gap.
GAP_STATES = ("publishing", "published", "pending_admin", "uncertain")
_VERSION_KEY = "fb_share:fb_version:{device_id}"
_ENQUEUED_KEY = "fb_share:phone_enqueued:{task_id}"
ENQUEUE_MARK_TTL_S = 900          # one queued copy per task; cleared when publish_task finishes


def _utc(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def _iso(now: datetime) -> str:
    return now.replace(microsecond=0).isoformat()


def _dispatch_enabled() -> bool:
    return os.environ.get("FB_SHARE_DISPATCH_ENABLED") == "1"


def _notify(client, message: str) -> None:
    try:
        client.notify_owner(message)
    except Exception:
        logger.warning("Group share: owner notification failed", exc_info=True)


def _default_cache():
    try:
        import redis

        from app.config import get_settings

        return redis.from_url(get_settings().redis_url, decode_responses=True)
    except Exception:
        logger.warning("Group share: Redis unavailable for the FB version cache", exc_info=True)
        return None


def remember_fb_version(cache, device_id: int, version: str, code: str = "") -> None:
    if cache is None or not version:
        return
    try:
        cache.set(_VERSION_KEY.format(device_id=device_id), f"{version}|{code}", ex=VERSION_CACHE_TTL_S)
    except Exception:
        logger.warning("Group share: FB version cache write failed", exc_info=True)


def current_fb_version(device: PhoneDevice, client, cache) -> str:
    """The phone's com.facebook.katana versionName (cached ≤ 10 min); "" when the probe cannot tell."""
    if cache is not None:
        try:
            cached = cache.get(_VERSION_KEY.format(device_id=device.id))
            if cached:
                return str(cached).split("|", 1)[0]
        except Exception:
            logger.warning("Group share: FB version cache read failed", exc_info=True)
    version, code = client.probe_fb_version(device)
    remember_fb_version(cache, device.id, version, code)
    return version


# ── Device lease (shared with social delivery phone posting) ─────────────────────────────────────────────────────


def acquire_lease(db, device_id: int, now: datetime) -> tuple[PhoneDevice | None, datetime | None, str]:
    """Take the device's exclusive lease (delivery_id NULL). Returns (device, acquired_at, reason)."""
    try:
        device = db.query(PhoneDevice).filter(PhoneDevice.id == device_id).with_for_update().populate_existing().first()
        if device is None or not device.adb_serial:
            db.rollback()
            return None, None, "phone not found or has no adb serial"
        if device.status != "available":
            db.rollback()
            return None, None, f"phone status is {device.status}"
        lease = db.query(DeviceLease).filter_by(device_id=device.id).first()
        if lease is not None and lease.expires_at > now:
            db.rollback()
            return None, None, "busy"
        if lease is None:
            lease = DeviceLease(device_id=device.id)
            db.add(lease)
        lease.delivery_id = None
        lease.acquired_at = now
        lease.expires_at = now + timedelta(minutes=LEASE_TTL_MINUTES)
        db.flush()
        return device, now, ""
    except IntegrityError:
        db.rollback()
        return None, None, "busy"


def release_lease(db, device_id: int, acquired_at: datetime) -> None:
    try:
        db.query(DeviceLease).filter(
            DeviceLease.device_id == device_id, DeviceLease.delivery_id.is_(None),
            DeviceLease.acquired_at == acquired_at,
        ).delete(synchronize_session=False)
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Group share: releasing the lease of device %s failed", device_id)


# ── Tick: select due phone tasks ─────────────────────────────────────────────────────────────────────────────────


def _sweep_stale_claims(db, now: datetime) -> None:
    stale = db.query(FbShareTask).filter(
        FbShareTask.delivery_mode == "phone_auto", FbShareTask.state == "publishing",
        FbShareTask.updated_at < now - timedelta(minutes=STALE_PUBLISHING_MINUTES),
    ).all()
    for task in stale:
        task.state = "uncertain"
        task.error = "worker stopped while posting; result unknown (never retried)"
        task.updated_at = now
    if stale:
        db.commit()


def _in_task_hours(delivery, destination, now: datetime) -> bool:
    try:
        local = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(destination.timezone or "UTC"))
        return caps.in_task_hours(local, delivery.task_hours_start, delivery.task_hours_end)
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        logger.warning("Invalid group share task window for destination %s", destination.id)
        return False


def _gap_elapsed(db, destination, now: datetime) -> bool:
    _enabled, settings = get_brand_share(db, destination.content_project_id)
    gap = timedelta(minutes=settings["caps"]["min_gap_minutes"])
    return db.query(FbShareTask.id).filter(
        FbShareTask.destination_id == destination.id, FbShareTask.delivery_mode == "phone_auto",
        FbShareTask.state.in_(GAP_STATES), FbShareTask.updated_at > now - gap,
    ).first() is None


def calibrated_for_phone(delivery) -> bool:
    """The stored calibration was rehearsed on the delivery's current phone (calibration is per phone)."""
    calibration = delivery.calibration if isinstance(delivery.calibration, dict) else {}
    return (delivery.calibrated_at is not None and bool(calibration.get("fb_version"))
            and delivery.phone_device_id is not None
            and calibration.get("phone_device_id") == delivery.phone_device_id)


def due_phone_rows(db, now: datetime) -> list:
    rows = db.query(FbShareTask, FbShareDelivery, SocialDestination).join(
        FbShareItem, FbShareItem.id == FbShareTask.item_id,
    ).join(FbShareDelivery, FbShareDelivery.destination_id == FbShareTask.destination_id).join(
        SocialDestination, SocialDestination.id == FbShareTask.destination_id,
    ).join(FbShareBrandSettings, FbShareBrandSettings.fanpage_id == SocialDestination.content_project_id).filter(
        FbShareBrandSettings.enabled.is_(True),
        FbShareDelivery.is_active.is_(True), FbShareDelivery.mode == "phone_auto",
        FbShareDelivery.calibrated_at.isnot(None), FbShareDelivery.phone_device_id.isnot(None),
        or_(FbShareDelivery.paused_until.is_(None), FbShareDelivery.paused_until < now),
        FbShareTask.state == "planned", FbShareTask.delivery_mode == "phone_auto",
        FbShareTask.scheduled_for <= now,
        or_(FbShareTask.next_retry_at.is_(None), FbShareTask.next_retry_at <= now),
        or_(FbShareItem.expires_at.is_(None), FbShareItem.expires_at >= now),
    ).order_by(FbShareTask.scheduled_for, FbShareTask.id).all()
    return [row for row in rows if calibrated_for_phone(row[1])]


def _mark_enqueued(cache, task_id: int) -> bool:
    """SET NX a short-lived mark so the tick queues one copy per task; True when this call may enqueue."""
    if cache is None:
        return True
    try:
        return bool(cache.set(_ENQUEUED_KEY.format(task_id=task_id), "1", nx=True, ex=ENQUEUE_MARK_TTL_S))
    except Exception:
        logger.warning("Group share: enqueue mark failed for task %s; enqueueing anyway", task_id, exc_info=True)
        return True


def _clear_enqueued(cache, task_id: int) -> None:
    if cache is None:
        return
    try:
        cache.delete(_ENQUEUED_KEY.format(task_id=task_id))
    except Exception:
        logger.warning("Group share: clearing the enqueue mark of task %s failed", task_id, exc_info=True)


def dispatch_phone_due(db, now: datetime, client, *, enqueue: Callable[[int], None] | None = None,
                       cache=None) -> int:
    """At most one due task per account per tick; `enqueue` hands it to the delivery worker (inline when None)."""
    now = _utc(now)
    _sweep_stale_claims(db, now)
    if enqueue is not None and cache is None:
        cache = _default_cache()
    seen = set()
    started = 0
    for task, delivery, destination in due_phone_rows(db, now):
        if destination.id in seen:
            continue
        seen.add(destination.id)
        if db.query(FbShareTask.id).filter(
            FbShareTask.destination_id == destination.id, FbShareTask.state == "publishing",
        ).first() is not None:
            continue
        if not _in_task_hours(delivery, destination, now) or not _gap_elapsed(db, destination, now):
            continue
        try:
            if enqueue is not None:
                if not _mark_enqueued(cache, task.id):
                    continue  # already queued; publish_task clears the mark when it finishes
                try:
                    enqueue(task.id)
                except Exception:
                    _clear_enqueued(cache, task.id)
                    raise
            else:
                publish_task(db, task.id, client, now, cache=cache)
            started += 1
        except Exception:
            db.rollback()
            logger.exception("Group share: phone task %s could not be started", task.id)
    return started


# ── Worker: one group post ───────────────────────────────────────────────────────────────────────────────────────


def _pause_for_recalibration(db, delivery, reason: str, now: datetime) -> None:
    delivery.calibration = {**(delivery.calibration or {}), "paused_reason": reason, "paused_at": _iso(now)}
    delivery.calibrated_at = None


def _media_payload(item) -> list[dict]:
    from .planner import _public_media_url

    kind = "video" if item.media_kind == "video" else "photo"
    paths = [path for path in (item.media or []) if isinstance(path, str) and path]
    return [{"url": _public_media_url(path), "kind": kind} for path in paths[:MAX_MEDIA]]


def _account_payload(db, destination) -> dict:
    parent = db.get(SocialDestination, destination.parent_profile_id) if destination.parent_profile_id else None
    return {
        "kind": destination.kind, "name": destination.name,
        "actor_id": str(destination.facebook_actor_id or "").strip(),
        "parent_profile_name": parent.name if parent is not None and destination.kind == "facebook_page" else None,
    }


def _caption(db, task) -> str:
    caption, hashtags = get_task_caption(db, task)
    caption, hashtags = (caption or "").strip(), (hashtags or "").strip()
    return f"{caption}\n{hashtags}" if hashtags else caption


def _set_membership(db, task, status: str, now: datetime) -> None:
    membership = db.query(FbGroupMembership).filter_by(destination_id=task.destination_id, group_id=task.group_id).first()
    if membership is None:
        membership = FbGroupMembership(destination_id=task.destination_id, group_id=task.group_id)
        db.add(membership)
    membership.status, membership.checked_at = status, now


def _retry_or_fail(task, detail: str, now: datetime) -> None:
    task.attempts = (task.attempts or 0) + 1
    task.error = detail[:500]
    if task.attempts > len(RETRY_DELAYS_MIN):
        task.state, task.next_retry_at = "failed", None
    else:
        task.state = "planned"
        task.next_retry_at = now + timedelta(minutes=RETRY_DELAYS_MIN[task.attempts - 1])


def apply_outcome(db, task, delivery, destination, result: dict, client, now: datetime) -> str:
    """Map one runner answer onto the task (and delivery/membership), commit, then notify the owner if needed."""
    outcome, detail = result.get("outcome"), result.get("detail") or ""
    task.updated_at = now
    notice = None
    if outcome == "published":
        task.state, task.post_url, task.error, task.next_retry_at = "published", result.get("post_url"), None, None
    elif outcome == "pending_admin":
        task.state, task.post_url, task.error, task.next_retry_at = "pending_admin", result.get("post_url"), None, None
    elif outcome == "not_member":
        task.state, task.error, task.next_retry_at = "not_member", detail[:500] or None, None
        _set_membership(db, task, "not_member", now)
    elif outcome == "blocked":
        task.state, task.error, task.next_retry_at = "blocked", detail[:500] or None, None
        delivery.paused_until = now + timedelta(hours=BLOCK_PAUSE_HOURS)
        delivery.block_count = (delivery.block_count or 0) + 1
        notice = f"Group Share: {destination.name} is blocked from posting in groups; paused {BLOCK_PAUSE_HOURS} h"
    elif outcome == "uncertain":
        task.state, task.error, task.next_retry_at = "uncertain", (detail or "result unknown")[:500], None
    elif outcome == "version_changed":
        old = (delivery.calibration or {}).get("fb_version") or "unknown"
        new = result.get("fb_version") or "unknown"
        task.state, task.error, task.next_retry_at = "planned", None, None
        _pause_for_recalibration(db, delivery, f"recalibrate: FB app changed {old} -> {new}", now)
        notice = f"Group Share: {destination.name} paused, FB app changed {old} -> {new}; calibrate again"
    elif outcome == "identity_failed":
        task.state, task.error, task.next_retry_at = "failed", (detail or "identity check failed")[:500], None
        _pause_for_recalibration(db, delivery, f"identity check failed: {detail}"[:255], now)
        notice = f"Group Share: {destination.name} paused, wrong account on the phone; check it and calibrate again"
    elif outcome == "busy":
        task.state = "planned"
    else:
        # failed, or an unexpected "rehearsed" answer to a real post (nothing was posted): retry, then fail.
        _retry_or_fail(task, detail or f"runner outcome {outcome}", now)
    db.commit()
    if notice:
        _notify(client, notice)
    return task.state


def _preflight(db, task, now: datetime):
    """Load and re-check everything for a claimed task. Returns (rows, reason); rows is None when not sendable."""
    item = db.get(FbShareItem, task.item_id)
    group = db.get(FbGroup, task.group_id)
    delivery = db.query(FbShareDelivery).filter_by(destination_id=task.destination_id).first()
    destination = db.get(SocialDestination, task.destination_id)
    if None in (item, group, delivery, destination):
        return None, "missing rows"
    enabled, _settings = get_brand_share(db, destination.content_project_id)
    if not enabled or not delivery.is_active or delivery.mode != "phone_auto" or delivery.phone_device_id is None:
        return None, "delivery not active in phone mode"
    if delivery.paused_until is not None and delivery.paused_until >= now:
        return None, "delivery paused"
    if not calibrated_for_phone(delivery):
        return None, "not calibrated on this phone"
    if not _in_task_hours(delivery, destination, now):
        return None, "outside task hours"
    return (item, group, delivery, destination), ""


def _terminal(task, state: str, error: str, now: datetime) -> str:
    task.state, task.error, task.updated_at, task.next_retry_at = state, error[:500], now, None
    return state


def _check_task(db, task, now: datetime):
    """Re-check a task. Returns (rows, None) when sendable, else (None, status). A terminal status ("expired",
    "failed") has already been written on the task (uncommitted); any other status leaves the task untouched."""
    if task is None or task.state != "planned" or task.delivery_mode != "phone_auto":
        return None, "not_planned"
    if task.next_retry_at is not None and task.next_retry_at > now or task.scheduled_for > now:
        return None, "not_due"
    rows, reason = _preflight(db, task, now)
    if rows is None:
        return None, f"skipped: {reason}"
    item, group, delivery, destination = rows
    if item.expires_at is not None and item.expires_at < now:
        return None, _terminal(task, "expired", "item expired before it was posted", now)
    caption = _caption(db, task)
    if not _media_payload(item) or not caption or len(caption) > MAX_CAPTION_CHARS:
        return None, _terminal(task, "failed", "invalid media or caption for a group post", now)
    if not str(destination.facebook_actor_id or "").strip():
        return None, _terminal(task, "failed", "account has no Facebook actor id; set it, then plan again", now)
    membership = db.query(FbGroupMembership).filter_by(destination_id=task.destination_id, group_id=task.group_id).first()
    if membership is None or membership.status != "joined":
        status = membership.status if membership is not None else "none"
        return None, _terminal(task, "expired", f"account is no longer a joined member of the group ({status})", now)
    return rows, None


TERMINAL_CHECK_STATES = ("expired", "failed")


def publish_task(db, task_id: int, client, now: datetime | None = None, *, cache=None) -> str:
    """Post one planned phone task through the runner. Returns a short status for logs."""
    if not _dispatch_enabled():
        return "dispatch_disabled"
    cache = cache if cache is not None else _default_cache()
    try:
        return _publish_task(db, task_id, client, _utc(now or datetime.now(timezone.utc)), cache)
    finally:
        _clear_enqueued(cache, task_id)


def _probe_unlocked(db, task_id: int, client, now: datetime, cache) -> tuple[str | None, str]:
    """Phase 1, no row lock and no open transaction during the HTTP probe (up to ~25 s): check the task, then read the
    phone's FB version. Returns (status to return now, or None to go on; current version, "" when not probed)."""
    task = db.query(FbShareTask).filter_by(id=task_id).populate_existing().first()
    rows, status = _check_task(db, task, now)
    if rows is None:
        db.rollback()  # terminal states are written only under the lock in phase 2
        return (None, "") if status in TERMINAL_CHECK_STATES else (status, "")
    device = db.get(PhoneDevice, rows[2].phone_device_id)
    if device is None or not device.adb_serial:
        db.rollback()
        return "skipped: phone missing", ""
    db.expunge(device)  # keep its loaded fields after the rollback below
    db.rollback()
    current = current_fb_version(device, client, cache)
    if not current:
        logger.warning("Group share: FB version of phone %s unknown; task %s waits", device.id, task_id)
        return "skipped: fb version unknown", ""
    return None, current


def _publish_task(db, task_id: int, client, now: datetime, cache) -> str:
    status, current = _probe_unlocked(db, task_id, client, now, cache)
    if status is not None:
        return status
    # Phase 2: lock the task row and re-check everything (state may have changed while probing).
    task = db.query(FbShareTask).filter_by(id=task_id).with_for_update().populate_existing().first()
    rows, status = _check_task(db, task, now)
    if rows is None:
        if status in TERMINAL_CHECK_STATES:
            db.commit()
        else:
            db.rollback()
        return status
    item, group, delivery, destination = rows
    if not current:
        db.rollback()  # the task became sendable between the phases; the next tick probes it
        return "skipped: fb version unknown"
    device = db.get(PhoneDevice, delivery.phone_device_id)
    if device is None or not device.adb_serial:
        db.rollback()
        return "skipped: phone missing"
    expected = delivery.calibration["fb_version"]
    if current != expected:
        _pause_for_recalibration(db, delivery, f"recalibrate: FB app changed {expected} -> {current}", now)
        db.commit()
        _notify(client, f"Group Share: {destination.name} paused, FB app changed {expected} -> {current}; calibrate again")
        return "version_changed"
    media, caption = _media_payload(item), _caption(db, task)
    device, acquired_at, reason = acquire_lease(db, device.id, now)
    if device is None:
        return f"skipped: {reason}"
    task.state, task.updated_at, task.error = "publishing", now, None
    db.commit()
    payload = {
        "task_id": task.id, "serial": device.adb_serial, "account": _account_payload(db, destination),
        "group": {"url": group.url, "name": group.name}, "media": media, "caption": caption,
        "rehearse": False, "expected_fb_version": expected,
    }
    db.rollback()  # reads only since the claim commit: no transaction stays open across the runner call
    try:
        try:
            result = client.group_publish(payload)
        except Exception as exc:
            result = {"outcome": "uncertain", "detail": f"client error {type(exc).__name__}"}
        if result.get("outcome") == "version_changed" and result.get("fb_version"):
            remember_fb_version(cache, device.id, result["fb_version"])
        state = apply_outcome(db, task, delivery, destination, result, client, now)
        logger.info("Group share: phone task %s -> %s (%s)", task.id, state, result.get("stage") or "")
        return state
    finally:
        release_lease(db, device.id, acquired_at)


# ── Calibration rehearsal ────────────────────────────────────────────────────────────────────────────────────────


def _calibration_group(db, destination_id: int):
    return db.query(FbGroup).join(FbGroupMembership, FbGroupMembership.group_id == FbGroup.id).filter(
        FbGroupMembership.destination_id == destination_id, FbGroupMembership.status == "joined",
        FbGroup.is_active.is_(True),
    ).order_by(FbGroup.id).first()


def _calibration_photo(db, brand_id: int) -> str | None:
    for item in db.query(FbShareItem).filter(
        FbShareItem.fanpage_id == brand_id, FbShareItem.media_kind.in_(("photo", "album")),
    ).order_by(FbShareItem.created_at.desc(), FbShareItem.id.desc()).limit(20):
        paths = [path for path in (item.media or []) if isinstance(path, str) and path]
        if paths:
            return paths[0]
    return None


def _store_calibration(delivery, result: dict, group_url: str | None, now: datetime,
                       device_id: int | None = None) -> bool:
    steps = result.get("steps") or []
    ok = (result.get("ok") is True and bool(result.get("fb_version")) and bool(steps)
          and all(step.get("ok") is True for step in steps))
    delivery.calibration = {
        "fb_version": result.get("fb_version") or "", "fb_version_code": result.get("fb_version_code") or "",
        "android": result.get("android") or "", "screen": result.get("screen") or "", "steps": steps,
        "calibrated_by": "rehearsal", "group_url": group_url, "ok": ok,
        "phone_device_id": device_id if device_id is not None else delivery.phone_device_id,
        "detail": result.get("detail") or ("" if ok else "calibration failed"), "checked_at": _iso(now),
    }
    delivery.calibrated_at = now if ok else None
    return ok


def _store_if_unchanged(db, destination_id: int, started_on: tuple, result: dict, group_url: str,
                        now: datetime) -> bool:
    """Store the rehearsal result only when the delivery still has the phone and mode it ran with (calibration is per
    phone); otherwise record the attempt and store nothing. Commits; returns True when a passing calibration landed."""
    current = db.query(FbShareDelivery).filter_by(
        destination_id=destination_id).with_for_update().populate_existing().first()
    if current is None or (current.id, current.mode, current.phone_device_id) != started_on:
        if current is not None:
            _record_last_attempt(current, "phone changed during calibration; calibrate again", now)
        db.commit()
        return False
    ok = _store_calibration(current, result, group_url, now, device_id=started_on[2])
    db.commit()
    return ok


def calibrate_delivery(db, destination_id: int, client, now: datetime | None = None, *, cache=None) -> str:
    """Rehearse one group post for an account (never posts). Returns calibrated | failed | busy | skipped."""
    now = _utc(now or datetime.now(timezone.utc))
    delivery = db.query(FbShareDelivery).filter_by(destination_id=destination_id).first()
    destination = db.get(SocialDestination, destination_id)
    if delivery is None or destination is None:
        return "skipped"
    if delivery.mode != "phone_auto" or delivery.phone_device_id is None:
        _store_calibration(delivery, {"detail": "choose phone posting and a phone first"}, None, now)
        db.commit()
        return "failed"
    started_on = (delivery.id, delivery.mode, delivery.phone_device_id)
    group = _calibration_group(db, destination_id)
    photo = _calibration_photo(db, destination.content_project_id)
    if group is None or photo is None:
        detail = "no joined group for this account" if group is None else "no recent photo to rehearse with"
        _store_calibration(delivery, {"detail": detail}, group.url if group else None, now)
        db.commit()
        return "failed"
    device, acquired_at, reason = acquire_lease(db, delivery.phone_device_id, now)
    if device is None:
        if reason == "busy":
            return "busy"
        _store_calibration(delivery, {"detail": reason}, group.url, now)
        db.commit()
        return "failed"
    db.commit()
    from .planner import _public_media_url

    payload = {
        "serial": device.adb_serial, "account": _account_payload(db, destination),
        "group": {"url": group.url, "name": group.name},
        "media": [{"url": _public_media_url(photo), "kind": "photo"}],
    }
    db.rollback()  # reads only since the lease commit: no transaction stays open across the rehearsal
    try:
        try:
            result = client.group_calibrate(payload)
        except Exception as exc:
            result = {"ok": False, "detail": f"client error {type(exc).__name__}"}
        if result.get("busy"):
            return "busy"
        ok = _store_if_unchanged(db, destination_id, started_on, result, group.url, now)
        if ok:
            remember_fb_version(cache if cache is not None else _default_cache(), device.id,
                                result["fb_version"], result.get("fb_version_code") or "")
        return "calibrated" if ok else "failed"
    finally:
        release_lease(db, device.id, acquired_at)


def record_calibration_busy(db, destination_id: int, now: datetime | None = None) -> None:
    """The phone stayed busy through every retry: note it, keep the existing calibration untouched."""
    now = _utc(now or datetime.now(timezone.utc))
    delivery = db.query(FbShareDelivery).filter_by(destination_id=destination_id).first()
    if delivery is None:
        return
    _record_last_attempt(delivery, "the phone was busy; calibration did not run, try again", now)
    db.commit()


def _record_last_attempt(delivery, detail: str, now: datetime) -> None:
    """Note a calibration attempt that stored nothing; the existing calibration (if any) stays untouched."""
    calibration = {key: value for key, value in (delivery.calibration or {}).items() if key != "requested_at"}
    delivery.calibration = {**calibration, "last_attempt": {"ok": False, "detail": detail, "checked_at": _iso(now)}}
