from __future__ import annotations

from datetime import datetime, timezone
import logging
from typing import Any
import zoneinfo

from sqlalchemy.orm import Session

from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services import mode_quota, publishing_schedule
from app.services.event_days import effective_daily_target

logger = logging.getLogger(__name__)


def destination_day_plan(
    db: Session,
    destination: SocialDestination,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    """Compute the today's plan breakdown and upcoming slots for a social destination."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    try:
        target_tz = zoneinfo.ZoneInfo(destination.timezone)
    except Exception:
        target_tz = zoneinfo.ZoneInfo("UTC")

    local_now = now_utc.astimezone(target_tz)
    local_date = local_now.date().isoformat()
    local_now_iso = local_now.isoformat()

    fanpage = None
    if destination.content_project_id:
        fanpage = db.get(TargetFanpage, destination.content_project_id)

    target = effective_daily_target(db, destination, fanpage, local_now.date())

    enabled = mode_quota.enabled_groups(fanpage) if fanpage else []
    quotas = mode_quota.group_quotas(
        target, destination.mode_shares, enabled
    )

    reservations_with_groups = mode_quota.group_reservations(db, destination)
    used: dict[str, int] = {}
    for aware_instant, grp in reservations_with_groups:
        inst_local = aware_instant.astimezone(target_tz)
        if inst_local.date() == local_now.date():
            group_key = grp if grp else "_unknown"
            used[group_key] = used.get(group_key, 0) + 1

    remaining: dict[str, int] = {}
    if quotas:
        for g, q in quotas.items():
            remaining[g] = max(0, q - used.get(g, 0))
        remaining_total = sum(remaining.values())
    else:
        remaining_total = max(0, target - sum(used.values()))

    # Free slots left today
    reservations = publishing_schedule.get_destination_reservations_utc(db, destination)
    phantoms: list[datetime] = []
    upcoming_slots: list[str] = []

    sleep_start = getattr(fanpage, "publish_sleep_start_hour", None) if fanpage else None
    sleep_end = getattr(fanpage, "publish_sleep_end_hour", None) if fanpage else None
    if sleep_start is None or sleep_end is None:
        sleep_start, sleep_end = None, None

    hour_weights = getattr(fanpage, "publish_hour_weights", None) if fanpage else None

    if target > 0 and remaining_total > 0:
        for _ in range(min(remaining_total, 30)):
            try:
                slot = publishing_schedule.compute_audience_slot(
                    now_utc=now_utc,
                    timezone_name=destination.timezone,
                    daily_target=target,
                    min_gap_minutes=destination.min_gap_minutes,
                    existing_slots_utc=reservations + phantoms,
                    sleep_start_hour=sleep_start,
                    sleep_end_hour=sleep_end,
                    namespace=f"dest-{destination.id}",
                    hour_weights=hour_weights,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to compute audience slot for destination %s: %s",
                    destination.id,
                    exc,
                )
                break

            slot_local = slot.astimezone(target_tz)
            if slot_local.date() != local_now.date():
                break

            phantoms.append(slot)
            upcoming_slots.append(slot.isoformat())

    return {
        "destination_id": destination.id,
        "name": destination.name,
        "timezone": destination.timezone,
        "local_now": local_now_iso,
        "local_date": local_date,
        "daily_target": target,
        "base_daily_target": destination.daily_target,
        "quotas": quotas,
        "used": used,
        "remaining": remaining,
        "remaining_total": remaining_total,
        "upcoming_slots": upcoming_slots,
    }
