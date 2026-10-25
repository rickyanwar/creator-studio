from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
import zoneinfo
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.content_groups import GROUP_IDS, enabled_groups, group_of
from app.services.event_days import effective_daily_target
from app.services.publishing_schedule import (
    _pick_slot_instant,
    build_legacy_reservations_query,
    compute_audience_slot,
    is_legacy_repliz_eligible,
)

logger = logging.getLogger(__name__)

WAITING_JOB_STATUSES = [
    PublishJobStatus.pending_watermark,
    PublishJobStatus.pending_caption,
    PublishJobStatus.pending_design,
    PublishJobStatus.rendering,
    PublishJobStatus.pending_review,
    PublishJobStatus.pending_publish,
    PublishJobStatus.publishing,
]
WAITING_STATUS_VALUES = [
    s.value if hasattr(s, "value") else str(s) for s in WAITING_JOB_STATUSES
]


def even_shares(groups: list[str] | None) -> dict[str, int]:
    """Calculate integer percentage shares (0..100) summing to 100 using largest remainder."""
    if not groups:
        return {}
    valid = [g for g in GROUP_IDS if g in groups]
    if not valid:
        return {}
    n = len(valid)
    ideal = 100.0 / n
    floors = {g: int(ideal) for g in valid}
    rem = {g: ideal - floors[g] for g in valid}
    remaining = 100 - sum(floors.values())
    sorted_groups = sorted(valid, key=lambda g: (-rem[g], GROUP_IDS.index(g)))
    for g in sorted_groups[:remaining]:
        floors[g] += 1
    return floors


def group_quotas(
    daily_target: int,
    shares: dict[str, int] | None = None,
    enabled: list[str] | None = None,
) -> dict[str, int]:
    """Calculate integer per-mode quotas using the largest remainder method.

    Returns {} when shares is None or empty (mode split is OFF).
    """
    if daily_target <= 0:
        daily_target = 0

    if not shares:
        return {}

    groups = [g for g in GROUP_IDS if g in shares]
    if not groups:
        return {}
    if daily_target == 0:
        return {g: 0 for g in groups}

    total_share = sum(shares[g] for g in groups)
    denom = float(total_share) if total_share > 0 else 100.0

    floors = {}
    rem = {}
    for g in groups:
        s = shares[g]
        if s <= 0:
            floors[g] = 0
            rem[g] = -1.0
        else:
            ideal = (daily_target * s) / denom
            fl = int(ideal)
            floors[g] = fl
            rem[g] = ideal - fl

    remaining = daily_target - sum(floors.values())
    if remaining > 0:
        eligible = [g for g in groups if shares[g] > 0]
        sorted_groups = sorted(eligible, key=lambda g: (-rem[g], GROUP_IDS.index(g)))
        for g in sorted_groups[:remaining]:
            floors[g] += 1
    return floors


def group_reservations(
    db: Session,
    destination: SocialDestination,
) -> list[tuple[datetime, str | None]]:
    """Retrieve reservations with group attribution for a destination."""
    reservations: list[tuple[datetime, str | None]] = []

    stmt = (
        select(
            PublishDelivery.scheduled_for,
            PublishDelivery.published_at,
            PublishDelivery.state,
            PublishJob.content_type,
        )
        .outerjoin(PublishJob, PublishDelivery.publish_job_id == PublishJob.id)
        .where(
            PublishDelivery.destination_id == destination.id,
            PublishDelivery.scheduled_for.is_not(None),
            PublishDelivery.state.not_in(["failed", "skipped"]),
        )
    )
    deliveries = db.execute(stmt).all()
    for scheduled_for, published_at, state, content_type in deliveries:
        instant = _pick_slot_instant(scheduled_for, published_at)
        if instant is not None:
            aware_instant = (
                instant.replace(tzinfo=timezone.utc)
                if instant.tzinfo is None
                else instant.astimezone(timezone.utc)
            )
            grp = group_of(content_type)
            reservations.append((aware_instant, grp))

    fanpage = getattr(destination, "content_project", None)
    if fanpage is None and destination.content_project_id:
        fanpage = (
            db.query(TargetFanpage)
            .filter_by(id=destination.content_project_id)
            .first()
        )

    if is_legacy_repliz_eligible(destination, fanpage):
        legacy_stmt = build_legacy_reservations_query(destination.content_project_id)
        legacy_jobs = db.execute(legacy_stmt).all()
        for scheduled_for, published_at, status in legacy_jobs:
            instant = _pick_slot_instant(scheduled_for, published_at)
            if instant is not None:
                aware_instant = (
                    instant.replace(tzinfo=timezone.utc)
                    if instant.tzinfo is None
                    else instant.astimezone(timezone.utc)
                )
                reservations.append((aware_instant, None))

    return reservations


def waiting_groups(
    db: Session,
    fanpage_id: int,
    destination_id: int,
    now_utc: datetime,
) -> set[str]:
    """Identify groups with active waiting content without deliveries for this destination."""
    aware_now = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
    naive_cutoff = aware_now.astimezone(timezone.utc).replace(
        tzinfo=None
    ) - timedelta(hours=24)

    delivered_job_ids = (
        select(PublishDelivery.publish_job_id)
        .where(PublishDelivery.destination_id == destination_id)
        .scalar_subquery()
    )

    stmt = (
        select(PublishJob.content_type)
        .where(
            PublishJob.fanpage_id == fanpage_id,
            PublishJob.status.in_(WAITING_STATUS_VALUES),
            PublishJob.created_at >= naive_cutoff,
            PublishJob.is_deleted.is_(False),
            PublishJob.id.not_in(delivered_job_ids),
        )
        .distinct()
    )

    rows = db.execute(stmt).all()
    result = set()
    for (ct,) in rows:
        grp = group_of(ct)
        if grp:
            result.add(grp)
    return result


def borrow_window(
    d: date,
    sleep_start: int | None,
    sleep_end: int | None,
    hours: int,
    tz: Any,
) -> tuple[datetime, datetime]:
    """Aware UTC [start, end) of the borrow window that belongs to local day d."""
    d_next = d + timedelta(days=1)
    if sleep_start is None:
        end_local = datetime(d_next.year, d_next.month, d_next.day, 0, 0, 0, tzinfo=tz)
        start_local = end_local - timedelta(hours=hours)
    elif sleep_end is not None and sleep_start > sleep_end:
        end_local = datetime(d.year, d.month, d.day, sleep_start, 0, 0, tzinfo=tz)
        wake_local = datetime(d.year, d.month, d.day, sleep_end, 0, 0, tzinfo=tz)
        start_local = max(end_local - timedelta(hours=hours), wake_local)
    else:
        end_local = datetime(d_next.year, d_next.month, d_next.day, 0, 0, 0, tzinfo=tz)
        wake_local = (
            datetime(d.year, d.month, d.day, sleep_end, 0, 0, tzinfo=tz)
            if sleep_end is not None
            else datetime(d.year, d.month, d.day, 0, 0, 0, tzinfo=tz)
        )
        sleep_start_dt = datetime(
            d_next.year, d_next.month, d_next.day, sleep_start, 0, 0, tzinfo=tz
        )
        start_local = max(wake_local, sleep_start_dt - timedelta(hours=hours))

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def pick_slot(
    db: Session,
    destination: SocialDestination,
    job: PublishJob,
    fanpage: TargetFanpage | None,
    now_utc: datetime,
    all_reservations: list[datetime],
    hour_weights: dict | None = None,
    sleep_start: int | None = None,
    sleep_end: int | None = None,
) -> datetime:
    """Find next slot enforcing per-mode quota and borrowing rules."""
    enabled = enabled_groups(fanpage) if fanpage else []
    local_day = now_utc.astimezone(zoneinfo.ZoneInfo(destination.timezone)).date()
    target = effective_daily_target(db, destination, fanpage, local_day)
    quotas = group_quotas(
        target, destination.mode_shares, enabled
    )
    g = group_of(job.content_type)

    if not quotas or g is None or g not in quotas:
        return compute_audience_slot(
            now_utc=now_utc,
            timezone_name=destination.timezone,
            daily_target=target,
            min_gap_minutes=destination.min_gap_minutes,
            existing_slots_utc=list(all_reservations),
            sleep_start_hour=sleep_start,
            sleep_end_hour=sleep_end,
            namespace=f"dest-{destination.id}",
            hour_weights=hour_weights,
        )

    tz = zoneinfo.ZoneInfo(destination.timezone)
    group_res = group_reservations(db, destination)
    waiting = waiting_groups(
        db, destination.content_project_id, destination.id, now_utc
    )

    borrow_hours = getattr(destination, "borrow_hours_before_sleep", 3)
    if borrow_hours is None:
        borrow_hours = 3

    phantoms: list[datetime] = []
    blocked_days: set[Any] = set()

    while len(blocked_days) < 14:
        current_existing = list(all_reservations) + phantoms
        slot = compute_audience_slot(
            now_utc=now_utc,
            timezone_name=destination.timezone,
            daily_target=target,
            min_gap_minutes=destination.min_gap_minutes,
            existing_slots_utc=current_existing,
            sleep_start_hour=sleep_start,
            sleep_end_hour=sleep_end,
            namespace=f"dest-{destination.id}",
            hour_weights=hour_weights,
        )
        d = slot.astimezone(tz).date()

        used_g = sum(
            1
            for instant, grp in group_res
            if grp == g and instant.astimezone(tz).date() == d
        )

        if used_g < quotas.get(g, 0):
            return slot

        # Try borrowing on day d
        if borrow_hours > 0:
            w_start_utc, w_end_utc = borrow_window(
                d, sleep_start, sleep_end, borrow_hours, tz
            )

            borrowable = 0
            for h in quotas:
                if h != g and h not in waiting:
                    used_h = sum(
                        1
                        for instant, grp in group_res
                        if grp == h and instant.astimezone(tz).date() == d
                    )
                    borrowable += max(0, quotas[h] - used_h)

            borrowed = max(0, used_g - quotas.get(g, 0))
            if borrowed < borrowable:
                s2 = compute_audience_slot(
                    now_utc=max(now_utc, w_start_utc),
                    timezone_name=destination.timezone,
                    daily_target=target,
                    min_gap_minutes=destination.min_gap_minutes,
                    existing_slots_utc=current_existing,
                    sleep_start_hour=sleep_start,
                    sleep_end_hour=sleep_end,
                    namespace=f"dest-{destination.id}",
                    hour_weights=hour_weights,
                )
                if s2.astimezone(tz).date() == d and w_start_utc <= s2 < w_end_utc:
                    return s2

        # Block day d: add phantom instants at local noon of day d until that day holds daily_target entries
        blocked_days.add(d)
        existing_on_d = sum(
            1 for dt in current_existing if dt.astimezone(tz).date() == d
        )
        needed = max(0, target - existing_on_d)
        noon_utc = datetime(d.year, d.month, d.day, 12, 0, 0, tzinfo=tz).astimezone(
            timezone.utc
        )
        for _ in range(needed):
            phantoms.append(noon_utc)

    logger.warning(
        "mode quota found no slot in 14 days for destination %s group %s; using the plain slot",
        destination.id,
        g,
    )
    return compute_audience_slot(
        now_utc=now_utc,
        timezone_name=destination.timezone,
        daily_target=target,
        min_gap_minutes=destination.min_gap_minutes,
        existing_slots_utc=list(all_reservations),
        sleep_start_hour=sleep_start,
        sleep_end_hour=sleep_end,
        namespace=f"dest-{destination.id}",
        hour_weights=hour_weights,
    )
