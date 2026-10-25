"""Spread already-scheduled AI videos to respect the fan page daily cap.

Follows owner decision (2026-10-26, explicit, one-off exception):
AI videos already scheduled at Repliz above the page's daily cap
(TargetFanpage.ai_video_daily_count, e.g. 3) must move to later days.
A schedule may be deleted at Repliz and re-planned ONLY after the delete
is confirmed. When in doubt, leave the delivery alone; a duplicate post
is worse than a moved one.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
import logging
import os
import sys
from typing import Any
import zoneinfo

import httpx
from sqlalchemy.orm import Session

# Add backend directory to sys.path so app can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from app.database import SessionLocal
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import DeliveryEvent, PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.mode_quota import ai_video_daily_cap, pick_slot
from app.services.publishing_schedule import get_destination_reservations_utc

logger = logging.getLogger(__name__)

COUNT_STATES = ("published", "repliz_scheduled", "publishing", "scheduled")


def _to_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def respread(
    db: Session,
    repliz_client: Any = None,
    *,
    now_utc: datetime | None = None,
    apply: bool = False,
    limit: int = 0,
    destination_id: int | None = None,
) -> list[dict]:
    """Spread already-scheduled AI videos beyond the fanpage daily cap."""
    if now_utc is None:
        aware_now = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        aware_now = now_utc.replace(tzinfo=timezone.utc)
    else:
        aware_now = now_utc.astimezone(timezone.utc)

    cutoff_aware = aware_now - timedelta(days=2)
    cutoff_naive = cutoff_aware.replace(tzinfo=None)
    lead_cutoff = aware_now + timedelta(minutes=30)

    dest_query = db.query(SocialDestination)
    if destination_id is not None:
        dest_query = dest_query.filter(SocialDestination.id == destination_id)
    else:
        dest_query = dest_query.filter(SocialDestination.is_active.is_(True))

    destinations = dest_query.order_by(SocialDestination.id.asc()).all()

    results: list[dict] = []
    moved_count = 0

    for dest in destinations:
        if limit > 0 and moved_count >= limit:
            break

        fanpage = getattr(dest, "content_project", None)
        if fanpage is None and dest.content_project_id:
            fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == dest.content_project_id).first()
        if not fanpage:
            continue

        cap = ai_video_daily_cap(fanpage)
        if cap is None:
            continue

        try:
            dest_tz = zoneinfo.ZoneInfo(dest.timezone)
        except Exception:
            dest_tz = timezone.utc

        deliveries = (
            db.query(PublishDelivery)
            .join(PublishJob, PublishDelivery.publish_job_id == PublishJob.id)
            .filter(
                PublishDelivery.destination_id == dest.id,
                PublishJob.content_type == ContentType.ai_video,
                PublishDelivery.state.in_(COUNT_STATES),
                PublishDelivery.scheduled_for.is_not(None),
                PublishDelivery.scheduled_for >= cutoff_naive,
            )
            .order_by(PublishDelivery.scheduled_for.asc())
            .all()
        )

        grouped_by_day: dict[Any, list[PublishDelivery]] = defaultdict(list)
        for delivery in deliveries:
            sched_aware = _to_aware(delivery.scheduled_for)
            if sched_aware < cutoff_aware:
                continue
            local_day = sched_aware.astimezone(dest_tz).date()
            grouped_by_day[local_day].append(delivery)

        for local_day in sorted(grouped_by_day.keys()):
            if limit > 0 and moved_count >= limit:
                break

            day_rows = grouped_by_day[local_day]
            count = len(day_rows)
            if count <= cap:
                continue

            movable_rows = [
                r
                for r in day_rows
                if r.state == "repliz_scheduled"
                and bool(r.repliz_schedule_id)
                and r.facebook_post_id is None
                and r.uncertain_since is None
                and _to_aware(r.scheduled_for) > lead_cutoff
            ]

            movable_rows.sort(key=lambda r: _to_aware(r.scheduled_for), reverse=True)

            needed = count - cap
            candidates = movable_rows[:needed]

            for row in candidates:
                if limit > 0 and moved_count >= limit:
                    break

                old_slot = row.scheduled_for
                old_schedule_id = row.repliz_schedule_id

                if not apply:
                    moved_count += 1
                    results.append({
                        "delivery_id": row.id,
                        "destination_id": dest.id,
                        "local_day": local_day,
                        "old_slot": old_slot,
                        "action": "would_move",
                        "new_slot": None,
                        "note": "dry run",
                    })
                    continue

                # 5.a Delete Repliz schedule
                try:
                    if repliz_client is None:
                        raise RuntimeError("Repliz client is not initialized")
                    repliz_client.delete_schedule(old_schedule_id)
                except Exception as exc:
                    results.append({
                        "delivery_id": row.id,
                        "destination_id": dest.id,
                        "local_day": local_day,
                        "old_slot": old_slot,
                        "action": "kept",
                        "new_slot": None,
                        "note": f"delete failed: {exc}",
                    })
                    continue

                # 5.b Verify deletion
                confirmed_deleted = False
                try:
                    sched_data = repliz_client.get_schedule(old_schedule_id)
                    if isinstance(sched_data, dict):
                        status_str = str(sched_data.get("status", "")).lower()
                        if status_str in {"deleted", "canceled", "cancelled"}:
                            confirmed_deleted = True
                except httpx.HTTPStatusError as exc:
                    code = getattr(getattr(exc, "response", None), "status_code", None)
                    if code is None:
                        code = getattr(exc, "status_code", None)
                    if code in (404, 410):
                        confirmed_deleted = True
                except Exception:
                    confirmed_deleted = False

                if not confirmed_deleted:
                    row.last_error = "respread: Repliz delete unverified, left as is"
                    db.commit()
                    results.append({
                        "delivery_id": row.id,
                        "destination_id": dest.id,
                        "local_day": local_day,
                        "old_slot": old_slot,
                        "action": "unverified",
                        "new_slot": None,
                        "note": "Repliz delete unverified, left as is",
                    })
                    continue

                # 5.c Confirmed: re-plan the slot
                all_reservations = get_destination_reservations_utc(db, dest)
                row_slot_utc = _to_aware(old_slot)
                for i, dt in enumerate(all_reservations):
                    if dt == row_slot_utc or abs((dt - row_slot_utc).total_seconds()) < 1.0:
                        all_reservations.pop(i)
                        break

                job = row.publish_job
                if job is None and row.publish_job_id:
                    job = db.query(PublishJob).filter(PublishJob.id == row.publish_job_id).first()

                try:
                    new_slot_dt = pick_slot(
                        db=db,
                        destination=dest,
                        job=job,
                        fanpage=fanpage,
                        now_utc=aware_now,
                        all_reservations=all_reservations,
                        hour_weights=getattr(fanpage, "publish_hour_weights", None),
                        sleep_start=getattr(fanpage, "publish_sleep_start_hour", None),
                        sleep_end=getattr(fanpage, "publish_sleep_end_hour", None),
                    )
                except Exception as exc:
                    # The Repliz schedule is already gone: never lose the video, push it one day later.
                    logger.warning("pick_slot failed for delivery %s, moving one day later: %s", row.id, exc)
                    new_slot_dt = _to_aware(old_slot) + timedelta(days=1)

                new_slot_naive = (
                    new_slot_dt.astimezone(timezone.utc).replace(tzinfo=None)
                    if new_slot_dt.tzinfo is not None
                    else new_slot_dt
                )

                row.state = "scheduled"
                row.repliz_schedule_id = None
                row.scheduled_for = new_slot_naive
                row.last_error = "respread: moved to respect the daily AI-video cap"

                if job is not None:
                    job.status = PublishJobStatus.pending_publish
                    if job.repliz_schedule_id == old_schedule_id:
                        job.repliz_schedule_id = None

                event = DeliveryEvent(
                    delivery_id=row.id,
                    event_type="respread_moved",
                    details={
                        "old_slot": old_slot.isoformat() if old_slot else None,
                        "new_slot": new_slot_naive.isoformat() if new_slot_naive else None,
                        "old_schedule_id": old_schedule_id,
                        "note": "respread: moved to respect the daily AI-video cap",
                    },
                )
                db.add(event)
                db.commit()

                moved_count += 1
                results.append({
                    "delivery_id": row.id,
                    "destination_id": dest.id,
                    "local_day": local_day,
                    "old_slot": old_slot,
                    "action": "moved",
                    "new_slot": new_slot_naive,
                    "note": "moved to respect the daily AI-video cap",
                })

    return results


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Spread already-scheduled AI videos to respect the fanpage daily cap."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply changes to Repliz and the database (default: dry-run).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Move at most N deliveries (default: 0 = no limit).",
    )
    parser.add_argument(
        "--destination",
        type=int,
        default=None,
        help="Only process this destination ID.",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> None:
    parsed = parse_args(args)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    db = SessionLocal()
    try:
        repliz_client = None
        if parsed.apply:
            try:
                from app.services.repliz_client import get_repliz_client_from_db
                repliz_client = get_repliz_client_from_db(db)
            except Exception:
                try:
                    from app.services.repliz_client import get_repliz_client_from_env
                    repliz_client = get_repliz_client_from_env()
                except Exception as exc:
                    logger.error("Could not initialize Repliz client: %s", exc)
                    sys.exit(1)

        results = respread(
            db,
            repliz_client,
            apply=parsed.apply,
            limit=parsed.limit,
            destination_id=parsed.destination,
        )

        for r in results:
            print(
                f"delivery_id={r['delivery_id']} destination_id={r['destination_id']} "
                f"local_day={r['local_day']} action={r['action']} old_slot={r['old_slot']} "
                f"new_slot={r['new_slot']} note={r['note']}"
            )

        counts = Counter(r["action"] for r in results)
        summary_str = ", ".join(f"{k}: {v}" for k, v in sorted(counts.items())) if counts else "none"
        print(f"Summary: {len(results)} considered ({summary_str})")

    except Exception as exc:
        logger.error("Error during respread: %s", exc, exc_info=True)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
