"""Pure audience-aware publishing schedule helper.

Implements deterministic daily candidate slots over active intervals using
weighted quantiles, bounded per-day/destination/index hash jitter, neighbor
min-gap separation, local-day caps, and explicit/default sleep windows.

Weighted hours heuristic:
Configurable-location baseline active hours default to 07:00–22:00 local time
when no explicit sleep window is configured on the project/destination.
Midday (12:00–14:00) and evening (18:00–21:00) receive modest weight (1.3x)
to favor realistic peak audience attention without clustering all deliveries
into a single peak. These are configurable-location baseline estimates, NOT
fetched Insights or proven best times.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import math
from typing import Sequence
import zoneinfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage

DEFAULT_ACTIVE_START_HOUR = 7
DEFAULT_ACTIVE_END_HOUR = 22
_MAX_BOUNDED_DAYS = 60
MIN_LEAD_SECONDS = 60


def get_hour_weight(hour: int) -> float:
    """Return relative activity weight for a 24-hour clock hour.

    Midday (12-14) and evening (18-21) receive modest boost (1.3x).
    """
    if 12 <= hour < 14:
        return 1.3
    if 18 <= hour < 21:
        return 1.3
    return 1.0


def _is_sleeping(hour: int, start: int, end: int) -> bool:
    """Return whether a local hour falls inside [start, end) sleep window."""
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def compute_audience_slot(
    now_utc: datetime,
    timezone_name: str,
    daily_target: int,
    min_gap_minutes: int | float,
    existing_slots_utc: Sequence[datetime],
    sleep_start_hour: int | None = None,
    sleep_end_hour: int | None = None,
    namespace: str = "default",
) -> datetime:
    """Compute the earliest valid aware UTC scheduled delivery instant.

    - Uses deterministic weighted quantiles over active hours with bounded jitter for all daily targets.
    - Prevents 5-minute bursts by distributing candidate slots across the local day.
    - Strictly enforces min_gap_minutes from ALL existing neighboring slots.
    - Enforces local calendar day cap (rollover to next day once capped).
    - Future reservations do not block earlier free days.
    - DST transitions roundtrip verified.
    - Constant >=60s lead floor for real execution and testing alike.
    """
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be a timezone-aware datetime")
    now_utc = now_utc.astimezone(timezone.utc)

    if not isinstance(namespace, str) or len(namespace) > 256:
        raise ValueError("namespace must be a string with length <= 256")

    if not isinstance(daily_target, int) or isinstance(daily_target, bool) or daily_target <= 0:
        raise ValueError("daily_target must be a positive integer")
    if daily_target > 1000:
        raise ValueError("daily_target exceeds maximum limit of 1000")

    if (
        isinstance(min_gap_minutes, bool)
        or not isinstance(min_gap_minutes, (int, float))
        or not math.isfinite(min_gap_minutes)
        or min_gap_minutes < 0
    ):
        raise ValueError("min_gap_minutes must be a finite non-negative number")

    try:
        target_tz = zoneinfo.ZoneInfo(timezone_name)
    except Exception as e:
        raise ValueError(f"Invalid timezone_name: {timezone_name}") from e

    clean_existing: list[datetime] = []
    if existing_slots_utc:
        for s in existing_slots_utc:
            if not isinstance(s, datetime) or s.tzinfo is None or s.utcoffset() is None:
                raise ValueError("existing_slots_utc items must be timezone-aware datetimes")
            clean_existing.append(s.astimezone(timezone.utc))

    if (sleep_start_hour is None) != (sleep_end_hour is None):
        raise ValueError("sleep_start_hour and sleep_end_hour must be specified together")
    if sleep_start_hour is not None and sleep_end_hour is not None:
        if (
            isinstance(sleep_start_hour, bool)
            or not isinstance(sleep_start_hour, int)
            or isinstance(sleep_end_hour, bool)
            or not isinstance(sleep_end_hour, int)
            or not (0 <= sleep_start_hour <= 23 and 0 <= sleep_end_hour <= 23)
        ):
            raise ValueError("sleep hours must be integers between 0 and 23")

    min_lead_utc = now_utc + timedelta(seconds=MIN_LEAD_SECONDS)
    start_date = now_utc.astimezone(target_tz).date()
    min_gap_sec = max(float(min_gap_minutes) * 60.0, 1.0)

    # Search up to _MAX_BOUNDED_DAYS local calendar days
    for day_offset in range(_MAX_BOUNDED_DAYS):
        current_date = start_date + timedelta(days=day_offset)

        # 1. Enforce local calendar day cap
        slots_on_day = sum(
            1 for s in clean_existing if s.astimezone(target_tz).date() == current_date
        )
        if slots_on_day >= daily_target:
            continue

        # 2. Determine active hours and compute minute weights for current day
        minute_weights: list[float] = [0.0] * 1440
        for m in range(1440):
            hr = m // 60
            if sleep_start_hour is not None and sleep_end_hour is not None:
                if _is_sleeping(hr, sleep_start_hour, sleep_end_hour):
                    continue
            else:
                if not (DEFAULT_ACTIVE_START_HOUR <= hr < DEFAULT_ACTIVE_END_HOUR):
                    continue
            minute_weights[m] = get_hour_weight(hr)

        active_minutes = [m for m in range(1440) if minute_weights[m] > 0]
        if not active_minutes:
            continue

        total_weight = sum(minute_weights[m] for m in active_minutes)
        if total_weight <= 0:
            continue

        # 3. Generate deterministic candidate slots across active window using fixed quantile grid
        day_candidates: list[datetime] = []
        cum_weights: list[float] = [0.0] * len(active_minutes)
        acc = 0.0
        for i, m in enumerate(active_minutes):
            acc += minute_weights[m]
            cum_weights[i] = acc

        num_active = len(active_minutes)
        for idx in range(daily_target):
            target_start = (idx / daily_target) * total_weight
            target_end = ((idx + 1) / daily_target) * total_weight

            k_start = 0
            while k_start < num_active and cum_weights[k_start] < target_start:
                k_start += 1
            k_end = k_start
            while k_end < num_active and cum_weights[k_end] < target_end:
                k_end += 1
            if k_end >= num_active:
                k_end = num_active - 1
            if k_start > k_end:
                k_start = k_end

            # Hash jitter bounded within [0.15, 0.85] of quantile cell
            h = hashlib.sha256(f"{current_date.isoformat()}:{namespace}:{idx}".encode("utf-8")).digest()
            rnd = int.from_bytes(h[:8], "big") / 0xFFFFFFFFFFFFFFFF
            jitter = 0.15 + 0.70 * rnd
            k_chosen = int(round(k_start + jitter * (k_end - k_start)))
            if k_chosen >= num_active:
                k_chosen = num_active - 1
            chosen_m = active_minutes[k_chosen]

            cand_local = datetime(
                current_date.year,
                current_date.month,
                current_date.day,
                chosen_m // 60,
                chosen_m % 60,
                0,
                tzinfo=target_tz,
            )

            # Validate DST roundtrip
            cand_utc = cand_local.astimezone(timezone.utc)
            roundtrip_local = cand_utc.astimezone(target_tz)
            if roundtrip_local.hour != cand_local.hour or roundtrip_local.minute != cand_local.minute:
                cand_utc = cand_utc.replace(second=0, microsecond=0)

            day_candidates.append(cand_utc)

        # 4. Filter candidates: must be >= min_lead_utc and >= min_gap from ALL existing slots
        valid_candidates: list[datetime] = []
        for cand in sorted(day_candidates):
            if cand < min_lead_utc:
                continue

            cand_loc = cand.astimezone(target_tz)
            if sleep_start_hour is not None and sleep_end_hour is not None:
                if _is_sleeping(cand_loc.hour, sleep_start_hour, sleep_end_hour):
                    continue
            else:
                if not (DEFAULT_ACTIVE_START_HOUR <= cand_loc.hour < DEFAULT_ACTIVE_END_HOUR):
                    continue

            # Check gap against all existing slots (neighbors before and after)
            separated = True
            for exist in clean_existing:
                if abs((cand - exist).total_seconds()) < min_gap_sec:
                    separated = False
                    break
            if not separated:
                continue

            valid_candidates.append(cand)

        if valid_candidates:
            return min(valid_candidates)

    raise RuntimeError("Failed to compute audience slot within bounded search limit")


def _pick_slot_instant(scheduled_for: datetime | None, published_at: datetime | None) -> datetime | None:
    """Choose authoritative slot instant between scheduled_for and published_at.

    Preserves future scheduled_for when published_at reflects earlier Repliz acceptance.
    Preserves actual execution spacing when published_at occurred after scheduled_for.
    """
    if scheduled_for is not None and published_at is not None:
        return max(scheduled_for, published_at)
    return scheduled_for if scheduled_for is not None else published_at


def is_legacy_repliz_eligible(destination: SocialDestination, fanpage: TargetFanpage | None) -> bool:
    """Return whether a destination inherits legacy PublishJob Repliz reservations.

    Only applies to Repliz or Mixed destinations whose repliz_account_id matches
    the project fanpage's Repliz account. Phone-only or mismatched destinations
    do not inherit project historical Repliz jobs.
    """
    if not fanpage or not getattr(fanpage, "repliz_account_id", None):
        return False
    fanpage_repliz = str(fanpage.repliz_account_id).strip()
    if not fanpage_repliz:
        return False

    if getattr(destination, "mode", None) not in ("repliz", "mixed"):
        return False

    dest_repliz = str(getattr(destination, "repliz_account_id", None) or "").strip()
    return bool(dest_repliz and dest_repliz == fanpage_repliz)


def build_legacy_reservations_query(content_project_id: int):
    """Construct PostgreSQL-safe query for legacy PublishJobs without ledger deliveries anywhere.

    Filters out soft-deleted jobs (is_deleted is True) and excludes jobs that already
    have any PublishDelivery rows (preventing sibling-destination double counts).
    Avoids FOR UPDATE on outer join and avoids DISTINCT on JSON columns.
    """
    all_ledger_job_ids = select(PublishDelivery.publish_job_id).scalar_subquery()
    return (
        select(PublishJob.scheduled_for, PublishJob.published_at, PublishJob.status)
        .where(
            PublishJob.fanpage_id == content_project_id,
            PublishJob.scheduled_for.is_not(None),
            PublishJob.is_deleted.is_(False),
            PublishJob.status.not_in([PublishJobStatus.failed, PublishJobStatus.skipped]),
            PublishJob.id.not_in(all_ledger_job_ids),
        )
    )


def build_deliveries_reservations_query(destination_id: int):
    """Construct query for active PublishDelivery rows of a destination."""
    return (
        select(PublishDelivery.scheduled_for, PublishDelivery.published_at, PublishDelivery.state)
        .where(
            PublishDelivery.destination_id == destination_id,
            PublishDelivery.scheduled_for.is_not(None),
            PublishDelivery.state.not_in(["failed", "skipped"]),
        )
    )


def build_reservations_query(destination_id: int, content_project_id: int):
    """Canonical query builder for legacy project reservations, reused by retrieval and dialect checks."""
    return build_legacy_reservations_query(content_project_id)


def get_destination_reservations_utc(
    db: Session,
    destination: SocialDestination,
) -> list[datetime]:
    """Retrieve all aware UTC reservations for a destination.

    Loads:
    1. Active PublishDelivery rows for this destination.
    2. Legacy PublishJob records for the project without ledger deliveries anywhere,
       strictly if the destination matches the project's legacy Repliz account.
    Uses _pick_slot_instant to preserve future reservations and reconciliation spacing.
    """
    reservations: list[datetime] = []

    # 1. Destination ledger deliveries
    deliv_stmt = build_deliveries_reservations_query(destination.id)
    deliveries = db.execute(deliv_stmt).all()
    for scheduled_for, published_at, state in deliveries:
        instant = _pick_slot_instant(scheduled_for, published_at)
        if instant is not None:
            reservations.append(instant.replace(tzinfo=timezone.utc))

    # 2. Legacy PublishJobs without ledger deliveries anywhere (Repliz-matching destinations only)
    fanpage = getattr(destination, "content_project", None)
    if fanpage is None and destination.content_project_id:
        fanpage = db.query(TargetFanpage).filter_by(id=destination.content_project_id).first()

    if is_legacy_repliz_eligible(destination, fanpage):
        legacy_stmt = build_legacy_reservations_query(destination.content_project_id)
        legacy_jobs = db.execute(legacy_stmt).all()
        for scheduled_for, published_at, status in legacy_jobs:
            instant = _pick_slot_instant(scheduled_for, published_at)
            if instant is not None:
                reservations.append(instant.replace(tzinfo=timezone.utc))

    return reservations
