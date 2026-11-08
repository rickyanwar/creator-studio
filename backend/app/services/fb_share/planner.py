"""Database planning and PIC bundle dispatch for group sharing."""

import logging
import os
import random
import re
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import or_

from app.config import get_settings
from app.models.fb_group_share import (
    FbGroup, FbGroupMembership, FbShareBrandSettings, FbShareDelivery, FbShareItem, FbShareTask,
)
from app.models.social_publishing import SocialDestination
from . import caps
from .brand_settings import get_brand_share
from .pic_messages import ONBOARDING_TEXT, build_task_messages
from .routing import select_groups
from .types import GroupInfo, SentRecord, ShareItem

logger = logging.getLogger(__name__)
_disabled_logged = False


def _utc(now):
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def _planning_timezone(destination):
    try:
        return ZoneInfo(destination.timezone or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def _task_window(slot, delivery, zone):
    local = slot.replace(tzinfo=timezone.utc).astimezone(zone)
    if caps.in_task_hours(local, delivery.task_hours_start, delivery.task_hours_end):
        return slot
    start = time.fromisoformat(delivery.task_hours_start)
    window = datetime.combine(local.date(), start, tzinfo=zone)
    if window < local:
        window += timedelta(days=1)
    return _utc(window)


def plan_brand(db, fanpage, now: datetime, rng: random.Random) -> int:
    enabled, settings = get_brand_share(db, fanpage.id)
    if not enabled:
        return 0
    now = _utc(now)
    limits = settings["caps"]
    accounts = db.query(FbShareDelivery, SocialDestination).join(
        SocialDestination, SocialDestination.id == FbShareDelivery.destination_id,
    ).filter(
        SocialDestination.content_project_id == fanpage.id,
        SocialDestination.kind.in_(("facebook_profile", "facebook_page")),
        FbShareDelivery.is_active.is_(True), FbShareDelivery.mode.isnot(None),
        or_(FbShareDelivery.paused_until.is_(None), FbShareDelivery.paused_until < now),
        or_(FbShareDelivery.mode != "phone_auto", FbShareDelivery.calibrated_at.isnot(None)),
    ).order_by(FbShareDelivery.id).all()
    brand_tasks = db.query(FbShareTask, FbShareItem).join(
        FbShareItem, FbShareItem.id == FbShareTask.item_id,
    ).join(SocialDestination, SocialDestination.id == FbShareTask.destination_id).filter(
        SocialDestination.content_project_id == fanpage.id,
    ).all()
    history = [SentRecord(task.group_id, item.phash, item.source_ref, task.scheduled_for)
               for task, item in brand_tasks if task.state not in ("expired", "failed", "not_member")]
    last_slots = {}
    for task, _ in brand_tasks:
        last_slots[task.destination_id] = max(last_slots.get(task.destination_id, task.scheduled_for), task.scheduled_for)
    groups = [GroupInfo(**{key: getattr(group, key) for key in GroupInfo.__dataclass_fields__})
              for group in db.query(FbGroup).filter(FbGroup.is_active.is_(True)).all()]
    joined = {destination.id: {row.group_id for row in db.query(FbGroupMembership).filter_by(
        destination_id=destination.id, status="joined",
    )} for _, destination in accounts}
    created = 0
    items = db.query(FbShareItem).filter_by(fanpage_id=fanpage.id, status="new").order_by(
        FbShareItem.created_at, FbShareItem.id,
    ).all()
    for item in items:
        if item.expires_at is not None and item.expires_at <= now:
            item.status = "expired"
            continue
        if not item.niche.strip() or item.sentiment is None:
            continue
        share_item = ShareItem(**{key: getattr(item, key) for key in ShareItem.__dataclass_fields__})
        for delivery, destination in accounts:
            zone = _planning_timezone(destination)
            today = now.replace(tzinfo=timezone.utc).astimezone(zone).date()
            day_start = _utc(datetime.combine(today, time.min, tzinfo=zone))
            day_end = _utc(datetime.combine(today + timedelta(days=1), time.min, tzinfo=zone))
            group_counts = defaultdict(int)
            account_count = 0
            for previous, _ in brand_tasks:
                if day_start <= previous.scheduled_for < day_end:
                    group_counts[previous.group_id] += 1
                    account_count += int(previous.destination_id == destination.id)
            enabled_since = delivery.calibrated_at if delivery.mode == "phone_auto" else delivery.created_at
            daily_cap = caps.effective_daily_cap(
                *(getattr(delivery, key) if getattr(delivery, key) is not None else limits[key]
                  for key in ("start_per_day", "weekly_increase", "max_per_day")),
                enabled_since.replace(tzinfo=timezone.utc).astimezone(zone).date(), today,
            )
            remaining = caps.remaining_today(daily_cap, account_count)
            eligible = [group for group in groups
                        if group_counts[group.id] < min(group.daily_cap, limits["per_group_per_day"])]
            chosen = select_groups(
                share_item, eligible, joined[destination.id],
                "page" if destination.kind == "facebook_page" else "profile",
                history, today.weekday(), max_groups=min(remaining, 5),
            )
            if not chosen:
                continue
            for group in chosen:
                slot = caps.next_slot(last_slots.get(destination.id), now, limits["min_gap_minutes"], rng)
                if delivery.mode in ("pic_whatsapp", "pic_telegram"):
                    slot = _task_window(slot, delivery, zone)
                entry = FbShareTask(item_id=item.id, group_id=group.id, destination_id=destination.id,
                                    delivery_mode=delivery.mode, scheduled_for=slot, state="planned")
                db.add(entry)
                brand_tasks.append((entry, item))
                last_slots[destination.id] = slot
                history.append(SentRecord(group.id, item.phash, item.source_ref, slot))
                created += 1
            item.status = "routed"
            break
    db.commit()
    return created


def _public_media_url(path):
    if path.startswith(("http://", "https://")):
        return path
    settings = get_settings()
    local = Path(path)
    if local.is_absolute():
        local = local.relative_to(Path(settings.storage_base_path))
    relative = local.as_posix().lstrip("/")
    if relative.startswith("media/"):
        relative = relative[len("media/"):]
    return f"{settings.storage_base_url.rstrip('/')}/{relative}"


def dispatch_due(db, now, client) -> int:
    global _disabled_logged
    if os.environ.get("FB_SHARE_DISPATCH_ENABLED") != "1":
        if not _disabled_logged:
            logger.info("Group share dispatch disabled")
            _disabled_logged = True
        return 0
    now = _utc(now)
    stale_claims = db.query(FbShareTask).filter(
        FbShareTask.state == "planned", FbShareTask.pic_message_ref.isnot(None),
        FbShareTask.updated_at < now - timedelta(minutes=30),
    ).all()
    for task in stale_claims:
        task.state = "uncertain"
        task.updated_at = now
    if stale_claims:
        db.commit()
    expired = db.query(FbShareTask).join(FbShareItem, FbShareItem.id == FbShareTask.item_id).filter(
        FbShareTask.state.in_(("planned", "sent_to_pic")), FbShareItem.expires_at < now,
    ).all()
    for task in expired:
        task.state = "expired"
    if expired:
        db.commit()
    rows = db.query(FbShareTask, FbShareItem, FbGroup, FbShareDelivery, SocialDestination).join(
        FbShareItem, FbShareItem.id == FbShareTask.item_id,
    ).join(FbGroup, FbGroup.id == FbShareTask.group_id).join(
        FbShareDelivery, FbShareDelivery.destination_id == FbShareTask.destination_id,
    ).join(SocialDestination, SocialDestination.id == FbShareTask.destination_id).join(
        FbShareBrandSettings, FbShareBrandSettings.fanpage_id == SocialDestination.content_project_id,
    ).filter(
        FbShareBrandSettings.enabled.is_(True),
        FbShareDelivery.is_active.is_(True),
        or_(FbShareDelivery.paused_until.is_(None), FbShareDelivery.paused_until < now),
        FbShareTask.state == "planned",
        FbShareTask.pic_message_ref.is_(None),
        FbShareTask.delivery_mode.in_(("pic_whatsapp", "pic_telegram")),
    ).order_by(FbShareTask.id).all()
    bundles = defaultdict(list)
    for row in rows:
        bundles[row[0].item_id, row[0].destination_id].append(row)
    sent = 0
    for (item_id, destination_id), bundle in bundles.items():
        task, item, _, delivery, destination = bundle[0]
        if min(row[0].scheduled_for for row in bundle) > now:
            continue
        if any(row[0].next_retry_at is not None and row[0].next_retry_at > now for row in bundle):
            continue
        try:
            local = now.replace(tzinfo=timezone.utc).astimezone(ZoneInfo(destination.timezone or "UTC"))
            if not caps.in_task_hours(local, delivery.task_hours_start, delivery.task_hours_end):
                continue
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            logger.warning("Invalid group share task window for destination %s", destination_id)
            continue
        already_sent = db.query(FbShareTask.id).filter(
            FbShareTask.item_id == item_id, FbShareTask.destination_id == destination_id,
            FbShareTask.pic_message_ref.isnot(None),
        ).first()
        if already_sent:
            continue
        try:
            messages = build_task_messages(
                page_name=destination.name, media_path=item.media[0], caption=item.caption or "",
                hashtags=item.hashtags or "", group_urls=[row[2].url for row in bundle],
            )
            payload = [{"kind": "text", "text": message.text} if message.kind == "text" else
                       {"kind": "media", "media_url": _public_media_url(message.media_path)} for message in messages]
            if not db.query(FbShareTask.id).filter(
                FbShareTask.destination_id == destination_id, FbShareTask.pic_message_ref.isnot(None),
                FbShareTask.state.notin_(("planned", "failed", "uncertain")),
            ).first():
                payload.insert(0, {"kind": "text", "text": ONBOARDING_TEXT})
            contact = delivery.pic_contact or ""
            if task.delivery_mode == "pic_whatsapp":
                contact = re.sub(r"[^0-9]", "", contact)
                channel = "whatsapp"
            else:
                channel = "telegram"
            if not contact:
                raise ValueError("PIC contact is required")
            for task, *_ in bundle:
                task.pic_message_ref = f"pic-{item_id}-{destination_id}"
                task.updated_at = now
            db.commit()
            ok, detail = client.send_pic_messages(f"{channel}:{contact}", payload)
        except Exception as exc:
            ok, detail = False, type(exc).__name__
        for task, *_ in bundle:
            if ok:
                task.state = "sent_to_pic"
                task.pic_message_ref = f"pic-{item_id}-{destination_id}"
                task.error = None
                task.next_retry_at = None
            else:
                task.pic_message_ref = None
                task.error = detail[:500]
                task.attempts += 1
                task.next_retry_at = now + timedelta(minutes=min(15 * 2 ** (task.attempts - 1), 240))
                if task.attempts >= 4:
                    task.state = "failed"
            task.updated_at = now
        db.commit()
        sent += int(ok)
    return sent
