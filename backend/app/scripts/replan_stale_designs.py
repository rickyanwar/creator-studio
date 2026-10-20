"""Script to replan stale finished news designs into free slots.

Finds candidates:
- PublishJob.status == pending_publish
- content_type == news_content
- all deliveries skipped with last_error starting with 'stale:'
- no external IDs (repliz_schedule_id or facebook_post_id) on deliveries

Re-runs ensure_deliveries for fresh jobs (< hours old, newest first).
Marks jobs that still don't fit (or are older than hours) as skipped.
Dry-run by default; commits only when --apply is passed.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
import os
import sys
from zoneinfo import ZoneInfo

# Add backend directory to sys.path so app can be imported
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery
from app.models.target_fanpages import TargetFanpage
from app.services.social_delivery_planner import ensure_deliveries

logger = logging.getLogger(__name__)
WIB = ZoneInfo("Asia/Jakarta")


def _format_wib(dt: datetime | None) -> str:
    if dt is None:
        return "none"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(WIB).strftime("%Y-%m-%d %H:%M WIB")


def _is_fresh(created_at: datetime | None, cutoff_utc: datetime) -> bool:
    if created_at is None:
        return False
    if created_at.tzinfo is not None:
        return created_at >= cutoff_utc
    return created_at >= cutoff_utc.replace(tzinfo=None)


def _sort_key_dt(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def replan_stale_designs(
    db: Session,
    *,
    apply: bool = False,
    hours: int = 24,
    fanpage_id: int | None = None,
    now_utc: datetime | None = None,
) -> dict[int, dict[str, int]]:
    """Replan stale finished news designs.

    Returns stats dict: {fanpage_id: {"planned": int, "no_slot": int, "too_old": int}}
    """
    if now_utc is None:
        aware_now = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        aware_now = now_utc.replace(tzinfo=timezone.utc)
    else:
        aware_now = now_utc.astimezone(timezone.utc)

    cutoff = aware_now - timedelta(hours=hours)

    stats: dict[int, dict[str, int]] = defaultdict(
        lambda: {"planned": 0, "no_slot": 0, "too_old": 0}
    )
    if fanpage_id is not None:
        _ = stats[fanpage_id]

    try:
        # Pre-load fanpage names for reporting
        fp_query = db.query(TargetFanpage.id, TargetFanpage.name)
        if fanpage_id is not None:
            fp_query = fp_query.filter(TargetFanpage.id == fanpage_id)
        fanpage_names = {r[0]: r[1] for r in fp_query.all()}

        # 1. Query candidate jobs
        job_query = db.query(PublishJob).filter(
            PublishJob.status == PublishJobStatus.pending_publish,
            PublishJob.content_type == ContentType.news_content,
            PublishJob.is_deleted.is_(False),
        )
        if fanpage_id is not None:
            job_query = job_query.filter(PublishJob.fanpage_id == fanpage_id)

        all_jobs = (
            job_query.order_by(
                PublishJob.fanpage_id.asc(),
                PublishJob.created_at.desc(),
            ).all()
        )

        fresh_candidates: list[PublishJob] = []
        old_candidates: list[PublishJob] = []

        for job in all_jobs:
            deliveries = job.deliveries
            if not deliveries:
                continue

            # Check for external IDs
            has_external_id = False
            for d in deliveries:
                if d.repliz_schedule_id or d.facebook_post_id:
                    print(
                        f"Skipping job {job.id}: delivery {d.id} has external id set "
                        f"(repliz_schedule_id={d.repliz_schedule_id}, facebook_post_id={d.facebook_post_id})"
                    )
                    has_external_id = True
                    break
            if has_external_id:
                continue

            # Must have at least 1 delivery, and ALL must be skipped with stale: error
            if not all(
                d.state == "skipped" and (d.last_error or "").startswith("stale:")
                for d in deliveries
            ):
                continue

            if _is_fresh(job.created_at, cutoff):
                fresh_candidates.append(job)
            else:
                old_candidates.append(job)

        # 2. Order fresh candidates by fanpage_id ASC, created_at DESC (newest first)
        fresh_candidates.sort(key=lambda j: _sort_key_dt(j.created_at), reverse=True)
        fresh_candidates.sort(key=lambda j: j.fanpage_id)

        # 3. Process fresh candidates
        for job in fresh_candidates:
            # Delete skipped delivery rows and flush
            for delivery in list(job.deliveries):
                db.delete(delivery)
            db.flush()
            db.expire(job, ["deliveries"])

            # Call ensure_deliveries
            new_deliveries = ensure_deliveries(db, job, now_utc=aware_now)

            # Check if all new deliveries are skipped with stale error
            if new_deliveries and all(
                d.state == "skipped" and (d.last_error or "").startswith("stale:")
                for d in new_deliveries
            ):
                job.status = PublishJobStatus.skipped
                job.last_error = new_deliveries[0].last_error
                stats[job.fanpage_id]["no_slot"] += 1
            else:
                stats[job.fanpage_id]["planned"] += 1
                created_wib = _format_wib(job.created_at)
                slot_strs = [
                    _format_wib(d.scheduled_for)
                    for d in new_deliveries
                    if d.scheduled_for
                ]
                slots_display = ", ".join(slot_strs) if slot_strs else "none"
                states_display = (
                    ", ".join(d.state for d in new_deliveries)
                    if new_deliveries
                    else "none"
                )
                fp_name = fanpage_names.get(job.fanpage_id, f"fanpage_{job.fanpage_id}")
                print(
                    f"{job.id} | {fp_name} | {created_wib} | {slots_display} | {states_display}"
                )

        # 4. Process old candidates (older than hours)
        for job in old_candidates:
            err = (
                job.deliveries[0].last_error
                if job.deliveries
                else "stale: no News slot within 24 h"
            )
            job.status = PublishJobStatus.skipped
            job.last_error = err
            stats[job.fanpage_id]["too_old"] += 1

        db.flush()

        # 5. Print totals per fanpage
        print("\nTotals per fanpage:")
        for fid in sorted(stats.keys()):
            counts = stats[fid]
            fp_name = fanpage_names.get(fid, f"Fanpage {fid}")
            print(
                f"  {fp_name} (ID: {fid}): "
                f"{counts['planned']} planned, {counts['no_slot']} no slot, {counts['too_old']} too old"
            )

        total_planned = sum(c["planned"] for c in stats.values())
        total_no_slot = sum(c["no_slot"] for c in stats.values())
        total_too_old = sum(c["too_old"] for c in stats.values())
        print(f"Total: {total_planned} planned, {total_no_slot} no slot, {total_too_old} too old\n")

        # 6. Apply or rollback
        if apply:
            db.commit()
            print("Changes committed.")
        else:
            db.rollback()
            print("DRY RUN - nothing saved")

        return dict(stats)

    except Exception:
        db.rollback()
        raise


def parse_args(args: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replan stale finished news designs into free slots."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help="Apply changes to the database (default: dry-run).",
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Maximum age in hours for fresh candidates (default: 24).",
    )
    parser.add_argument(
        "--fanpage-id",
        type=int,
        default=None,
        help="Only process this fanpage ID.",
    )
    return parser.parse_args(args)


def main(args: list[str] | None = None) -> None:
    parsed = parse_args(args)
    db = SessionLocal()
    try:
        replan_stale_designs(
            db,
            apply=parsed.apply,
            hours=parsed.hours,
            fanpage_id=parsed.fanpage_id,
        )
    except Exception as exc:
        print(f"Error during replan: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
