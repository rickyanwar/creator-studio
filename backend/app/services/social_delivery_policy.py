"""Pure social delivery policy module.

Implements route validation, method selection, next slot calculation,
fallback gating, approval timing, and publish identity assertion.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence
import zoneinfo


def validate_route(
    kind: str,
    mode: str,
    parent_profile_id: str | None,
    repliz_account_id: str | None,
    daily_target: int,
    phone_daily_target: int,
    repliz_daily_target: int,
) -> bool:
    """Validate social destination route, hierarchy, and method quotas."""
    if kind not in ("profile", "page"):
        raise ValueError(f"Unsupported kind: {kind}")
    if mode not in ("phone", "repliz", "mixed"):
        raise ValueError(f"Unsupported mode: {mode}")

    for val in (daily_target, phone_daily_target, repliz_daily_target):
        if not isinstance(val, int) or isinstance(val, bool):
            raise ValueError("Daily target and quotas must be integers")

    if daily_target <= 0:
        raise ValueError("daily_target must be positive")
    if phone_daily_target < 0 or repliz_daily_target < 0:
        raise ValueError("Quotas must be non-negative")
    if phone_daily_target + repliz_daily_target != daily_target:
        raise ValueError(
            f"Quota sum ({phone_daily_target} + {repliz_daily_target}) must equal daily_target ({daily_target})"
        )

    if kind == "profile":
        if mode != "phone":
            raise ValueError("Profile routes must be phone mode")
        if parent_profile_id is not None and str(parent_profile_id).strip():
            raise ValueError("Profile routes cannot have a parent profile")
        if repliz_account_id is not None and str(repliz_account_id).strip():
            raise ValueError("Profile routes cannot have a repliz account")
        if repliz_daily_target != 0 or phone_daily_target != daily_target:
            raise ValueError("Profile routes must allocate full daily target to phone")
        return True

    # kind == "page"
    if mode == "phone":
        if not parent_profile_id or not str(parent_profile_id).strip():
            raise ValueError("Page phone mode requires a parent profile")
        if repliz_daily_target != 0:
            raise ValueError("Page phone mode cannot have repliz quota")
    elif mode == "repliz":
        if not repliz_account_id or not str(repliz_account_id).strip():
            raise ValueError("Page repliz mode requires a repliz account")
        if phone_daily_target != 0:
            raise ValueError("Page repliz mode cannot have phone quota")
    elif mode == "mixed":
        if not parent_profile_id or not str(parent_profile_id).strip():
            raise ValueError("Page mixed mode requires a parent profile")
        if not repliz_account_id or not str(repliz_account_id).strip():
            raise ValueError("Page mixed mode requires a repliz account")
        if phone_daily_target <= 0 or repliz_daily_target <= 0:
            raise ValueError("Page mixed mode requires positive phone and repliz quotas")

    return True


def choose_method_for_slot(
    mode: str,
    slot_index: int,
    phone_daily_target: int,
    repliz_daily_target: int,
) -> str:
    """Return 'phone' or 'repliz' for a specific slot index based on mode and quotas."""
    if not isinstance(slot_index, int) or isinstance(slot_index, bool) or slot_index < 0:
        raise ValueError(f"Invalid slot_index: {slot_index}. Must be a non-negative integer")

    if mode not in ("phone", "repliz", "mixed"):
        raise ValueError(f"Invalid mode: {mode}")

    for q in (phone_daily_target, repliz_daily_target):
        if not isinstance(q, int) or isinstance(q, bool) or q < 0:
            raise ValueError("Quotas must be non-negative integers")

    if mode == "phone":
        return "phone"
    if mode == "repliz":
        return "repliz"

    # mode == "mixed"
    if phone_daily_target <= 0 or repliz_daily_target <= 0:
        raise ValueError("Mixed mode requires positive phone and repliz daily targets")

    cycle_length = phone_daily_target + repliz_daily_target
    idx = slot_index % cycle_length
    if idx < phone_daily_target:
        return "phone"
    return "repliz"


def _is_sleeping(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def compute_next_slot(
    now_utc: datetime,
    timezone_name: str,
    daily_target: int,
    min_gap_minutes: int | float,
    existing_slots_utc: Sequence[datetime],
    sleep_start_hour: int | None = None,
    sleep_end_hour: int | None = None,
    publish_hour_weights: dict[str, float] | dict[int, float] | None = None,
    hour_weights: dict[str, float] | dict[int, float] | None = None,
) -> datetime:
    """Compute the earliest valid aware UTC scheduled delivery instant."""
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be a timezone-aware datetime")
    now_utc = now_utc.astimezone(timezone.utc)

    if not isinstance(daily_target, int) or isinstance(daily_target, bool) or daily_target <= 0:
        raise ValueError("daily_target must be a positive integer")

    if not isinstance(min_gap_minutes, (int, float)) or min_gap_minutes < 0:
        raise ValueError("min_gap_minutes must be a non-negative number")

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
        if not (0 <= sleep_start_hour <= 23 and 0 <= sleep_end_hour <= 23):
            raise ValueError("sleep hours must be between 0 and 23")

    weights_input = publish_hour_weights or hour_weights
    if not weights_input:
        earliest = now_utc
        if clean_existing:
            latest = max(clean_existing)
            gap_target = latest + timedelta(minutes=min_gap_minutes)
            if gap_target > earliest:
                earliest = gap_target

        cand = earliest

        # Ponytail: linear daily rollover search bounded at 365 days; upgrade to direct interval math if targets span years.
        for _ in range(1000):
            if sleep_start_hour is not None and sleep_end_hour is not None and sleep_start_hour != sleep_end_hour:
                cand_loc = cand.astimezone(target_tz)
                if _is_sleeping(cand_loc.hour, sleep_start_hour, sleep_end_hour):
                    if sleep_start_hour < sleep_end_hour:
                        wake_date = cand_loc.date()
                    else:
                        if cand_loc.hour >= sleep_start_hour:
                            wake_date = cand_loc.date() + timedelta(days=1)
                        else:
                            wake_date = cand_loc.date()
                    wake_loc = datetime(
                        wake_date.year,
                        wake_date.month,
                        wake_date.day,
                        sleep_end_hour,
                        0,
                        0,
                        tzinfo=target_tz,
                    )
                    wake_utc = wake_loc.astimezone(timezone.utc)
                    if wake_utc > cand:
                        cand = wake_utc

            cand_loc = cand.astimezone(target_tz)
            cand_date = cand_loc.date()

            slots_on_day = sum(
                1 for s in clean_existing if s.astimezone(target_tz).date() == cand_date
            )
            if slots_on_day >= daily_target:
                next_day = cand_date + timedelta(days=1)
                next_day_loc = datetime(
                    next_day.year, next_day.month, next_day.day, 0, 0, 0, tzinfo=target_tz
                )
                cand = next_day_loc.astimezone(timezone.utc)
                continue

            return cand.astimezone(timezone.utc)

        raise RuntimeError("Failed to compute next slot within iteration limit")

    # Weighted slot planning when publish_hour_weights is present
    weights: dict[int, float] = {}
    for h in range(24):
        if str(h) in weights_input:
            weights[h] = float(weights_input[str(h)])
        elif h in weights_input:
            weights[h] = float(weights_input[h])
        else:
            weights[h] = 1.0

    earliest = now_utc
    if clean_existing:
        latest = max(clean_existing)
        gap_target = latest + timedelta(minutes=min_gap_minutes)
        if gap_target > earliest:
            earliest = gap_target

    cand_date = earliest.astimezone(target_tz).date()
    step_minutes = max(int(min_gap_minutes), 1) if min_gap_minutes < 60 else 60

    # Ponytail: search bounded at 1000 days; rollover to next local calendar day when day is capped.
    for _ in range(1000):
        slots_on_day = sum(
            1 for s in clean_existing if s.astimezone(target_tz).date() == cand_date
        )
        if slots_on_day >= daily_target:
            cand_date += timedelta(days=1)
            continue

        allowed_hours = [
            h for h in range(24)
            if not (
                sleep_start_hour is not None
                and sleep_end_hour is not None
                and sleep_start_hour != sleep_end_hour
                and _is_sleeping(h, sleep_start_hour, sleep_end_hour)
            )
        ]
        if not allowed_hours:
            cand_date += timedelta(days=1)
            continue

        candidate_points: list[tuple[int, int]] = []
        if step_minutes < 60:
            for h in allowed_hours:
                for m in range(0, 60, step_minutes):
                    candidate_points.append((h, m))
        else:
            for h in allowed_hours:
                candidate_points.append((h, 0))

        # Rank candidates by descending hour weight, then chronological
        candidate_points.sort(key=lambda item: (-weights.get(item[0], 1.0), item[0], item[1]))

        selected_points: list[tuple[int, int]] = []
        for h, m in candidate_points:
            cand_dt = datetime(cand_date.year, cand_date.month, cand_date.day, h, m, 0, tzinfo=target_tz)
            conflict = False
            for sel_h, sel_m in selected_points:
                sel_dt = datetime(cand_date.year, cand_date.month, cand_date.day, sel_h, sel_m, 0, tzinfo=target_tz)
                if abs((cand_dt - sel_dt).total_seconds()) < min_gap_minutes * 60:
                    conflict = True
                    break
            if not conflict:
                selected_points.append((h, m))
                if len(selected_points) >= daily_target:
                    break

        selected_points.sort(key=lambda item: (item[0], item[1]))

        found_slot: datetime | None = None
        for h, m in selected_points:
            cand_loc = datetime(cand_date.year, cand_date.month, cand_date.day, h, m, 0, tzinfo=target_tz)
            earliest_loc = earliest.astimezone(target_tz)
            if cand_loc.date() == earliest_loc.date() and cand_loc.hour == earliest_loc.hour and earliest_loc > cand_loc:
                cand_loc = earliest_loc

            cand_utc = cand_loc.astimezone(timezone.utc)
            if cand_utc < earliest:
                continue

            cand_loc_final = cand_utc.astimezone(target_tz)
            if sleep_start_hour is not None and sleep_end_hour is not None and sleep_start_hour != sleep_end_hour:
                if _is_sleeping(cand_loc_final.hour, sleep_start_hour, sleep_end_hour):
                    continue

            gap_ok = True
            for s in clean_existing:
                if abs((cand_utc - s).total_seconds()) < min_gap_minutes * 60:
                    gap_ok = False
                    break
            if not gap_ok:
                continue

            found_slot = cand_utc
            break

        if found_slot is not None:
            return found_slot

        cand_date += timedelta(days=1)

    raise RuntimeError("Failed to compute next slot within iteration limit")


def fallback_allowed(
    kind: str,
    incident_state: str | None,
    uncertain: bool,
    repliz_connected: bool,
    content_supported: bool,
    absence_verified: bool,
) -> bool:
    """Return whether automated fallback to Repliz is permitted."""
    return (
        kind == "page"
        and incident_state == "needs_human"
        and not uncertain
        and bool(repliz_connected)
        and bool(content_supported)
        and bool(absence_verified)
    )


def approval_state(
    notified_at: datetime,
    now_utc: datetime,
    reply: str | None = None,
) -> str:
    """Determine approval state for an incident repair notification."""
    if not isinstance(notified_at, datetime) or notified_at.tzinfo is None or notified_at.utcoffset() is None:
        raise ValueError("notified_at must be a timezone-aware datetime")
    if not isinstance(now_utc, datetime) or now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be a timezone-aware datetime")

    if reply is not None:
        if reply in ("approved", "denied"):
            return reply
        raise ValueError(f"Invalid reply: {reply}. Must be 'approved', 'denied', or None")

    elapsed_seconds = (now_utc - notified_at).total_seconds()
    if elapsed_seconds >= 15 * 60:
        return "approved"
    return "waiting"


def assert_publish_identity(
    expected_profile_id: str | None,
    observed_profile_id: str | None,
    target_kind: str,
    expected_page_id: str | None,
    observed_page_id: str | None,
    calibration_valid: bool,
) -> bool:
    """Assert device calibration and stable Facebook profile/page identity match expected target."""
    if not calibration_valid:
        raise ValueError("Device calibration is invalid")

    if target_kind not in ("profile", "page"):
        raise ValueError(f"Unknown target_kind: {target_kind}")

    if not expected_profile_id or not str(expected_profile_id).strip():
        raise ValueError("Missing expected profile ID")
    if not observed_profile_id or not str(observed_profile_id).strip():
        raise ValueError("Missing observed profile ID")
    if str(expected_profile_id).strip() != str(observed_profile_id).strip():
        raise ValueError(
            f"Profile ID mismatch: expected {expected_profile_id}, observed {observed_profile_id}"
        )

    if target_kind == "page":
        if not expected_page_id or not str(expected_page_id).strip():
            raise ValueError("Missing expected page ID")
        if not observed_page_id or not str(observed_page_id).strip():
            raise ValueError("Missing observed page ID")
        if str(expected_page_id).strip() != str(observed_page_id).strip():
            raise ValueError(
                f"Page ID mismatch: expected {expected_page_id}, observed {observed_page_id}"
            )

    return True
