import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.models.settings import Settings
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.social_publishing import PublishDelivery
from app.models.target_fanpages import TargetFanpage
from app.services.post_metrics import (
    TRACK_WINDOW_MINUTES,
    BACKFILL_BUCKET,
    due_bucket,
    parse_statistic,
    extract_post_id,
    select_metric_targets,
    select_post_id_and_account,
)
from app.services.repliz_client import get_repliz_client_from_db, ReplizPlanRequired
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_CALL_PAUSE_SECONDS = 0.3
_COLLECT_LIMIT = 100
_BACKFILL_LIMIT = 150
_MAX_ERRORS = 10


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _should_skip(db: Session, now: datetime) -> tuple[bool, Settings | None]:
    settings = db.query(Settings).filter_by(id=1).first()
    if not settings or not settings.metrics_ingestion_enabled:
        return True, settings
    if settings.metrics_plan_status == "plan_required" and settings.metrics_plan_checked_at:
        if (now - settings.metrics_plan_checked_at) < timedelta(hours=6):
            return True, settings
    return False, settings


def _fetch_and_store(db: Session, settings: Settings, due_list: list, now: datetime) -> None:
    client = get_repliz_client_from_db(db)
    err_count = 0
    success_count = 0

    for i, item in enumerate(due_list):
        if len(item) == 6:
            job, delivery_id, post_id, account_id, bucket, age_mins = item
        elif len(item) == 5:
            job, post_id, account_id, bucket, age_mins = item
            delivery_id = None
        else:
            job, post_id, bucket, age_mins = item
            delivery_id = None
            account_id = job.fanpage.repliz_account_id if job.fanpage else None

        if i > 0:
            time.sleep(_CALL_PAUSE_SECONDS)

        if not account_id:
            continue

        try:
            raw_json = client.get_content_statistic(post_id, account_id)
            counts = parse_statistic(raw_json)

            values = dict(
                publish_job_id=job.id,
                delivery_id=delivery_id,
                fanpage_id=job.fanpage_id,
                bucket=bucket,
                captured_at=now,
                age_minutes=age_mins,
                likes=counts.likes,
                comments=counts.comments,
                shares=counts.shares,
                raw_json=raw_json,
            )
            stmt = insert(PostMetricSnapshot).values(**values)
            if delivery_id is not None:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["delivery_id", "bucket"],
                    index_where=PostMetricSnapshot.delivery_id.isnot(None),
                )
            else:
                stmt = stmt.on_conflict_do_nothing(
                    index_elements=["publish_job_id", "bucket"],
                    index_where=PostMetricSnapshot.delivery_id.is_(None),
                )
            
            db.execute(stmt)
            success_count += 1
            
            settings.metrics_plan_status = "ok"
            settings.metrics_plan_checked_at = now
            settings.metrics_last_error = None

            if success_count % 20 == 0:
                db.commit()

        except ReplizPlanRequired:
            settings.metrics_plan_status = "plan_required"
            settings.metrics_plan_checked_at = now
            db.commit()
            return
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                db.commit()
                return
            err_count += 1
            logger.warning("Error fetching metrics for %s: %s", job.id, e)
            if err_count > _MAX_ERRORS:
                settings.metrics_plan_status = "error"
                settings.metrics_last_error = str(e)
                db.commit()
                return
        except Exception as e:
            err_count += 1
            logger.warning("Error fetching metrics for %s: %s", job.id, e)
            if err_count > _MAX_ERRORS:
                settings.metrics_plan_status = "error"
                settings.metrics_last_error = str(e)
                db.commit()
                return

    db.commit()


@celery_app.task(name="app.tasks.post_metrics.collect_post_metrics", ignore_result=True)
def collect_post_metrics():
    db = SessionLocal()
    try:
        now = _utc_now()
        skip, settings = _should_skip(db, now)
        if skip:
            return

        window_start = now - timedelta(minutes=TRACK_WINDOW_MINUTES)
        window_end = now - timedelta(minutes=60)

        has_post_id_filter = or_(
            func.json_extract_path_text(PublishJob.repliz_response_json, "postId").isnot(None),
            PublishJob.deliveries.any(PublishDelivery.facebook_post_id.isnot(None)),
        )

        jobs = (
            db.query(PublishJob)
            .join(TargetFanpage)
            .options(
                joinedload(PublishJob.fanpage),
                joinedload(PublishJob.deliveries).joinedload(PublishDelivery.destination),
            )
            .filter(
                PublishJob.status == PublishJobStatus.published,
                PublishJob.is_deleted == False,
                PublishJob.scheduled_for >= window_start,
                PublishJob.scheduled_for <= window_end,
                has_post_id_filter,
            )
            .order_by(PublishJob.scheduled_for.desc())
            .all()
        )

        job_ids = [j.id for j in jobs]
        if not job_ids:
            return

        snapshots = (
            db.query(
                PostMetricSnapshot.publish_job_id,
                PostMetricSnapshot.delivery_id,
                PostMetricSnapshot.bucket,
            )
            .filter(PostMetricSnapshot.publish_job_id.in_(job_ids))
            .all()
        )

        legacy_taken_map: dict[int, set[str]] = {}
        delivery_taken_map: dict[int, set[str]] = {}
        for jid, did, b in snapshots:
            if did is not None:
                delivery_taken_map.setdefault(did, set()).add(b)
            else:
                legacy_taken_map.setdefault(jid, set()).add(b)

        due_list = []
        for job in jobs:
            targets = select_metric_targets(job)
            if not targets:
                continue

            age_mins = int((now - job.scheduled_for).total_seconds() // 60)
            for delivery_id, post_id, account_id in targets:
                if not post_id or not account_id:
                    continue
                if delivery_id is not None:
                    taken = delivery_taken_map.get(delivery_id, set())
                else:
                    taken = legacy_taken_map.get(job.id, set())

                bucket = due_bucket(age_mins, taken)
                if bucket:
                    due_list.append((job, delivery_id, post_id, account_id, bucket, age_mins))
                    if len(due_list) >= _COLLECT_LIMIT:
                        break
            if len(due_list) >= _COLLECT_LIMIT:
                break

        if not due_list:
            return

        _fetch_and_store(db, settings, due_list, now)
    finally:
        db.close()


@celery_app.task(name="app.tasks.post_metrics.backfill_post_metrics", ignore_result=True)
def backfill_post_metrics():
    db = SessionLocal()
    try:
        now = _utc_now()
        skip, settings = _should_skip(db, now)
        if skip:
            return

        window_start = now - timedelta(minutes=TRACK_WINDOW_MINUTES)

        has_post_id_filter = or_(
            func.json_extract_path_text(PublishJob.repliz_response_json, "postId").isnot(None),
            PublishJob.deliveries.any(PublishDelivery.facebook_post_id.isnot(None)),
        )

        backfilled_legacy = (
            db.query(PostMetricSnapshot.publish_job_id)
            .filter(
                PostMetricSnapshot.delivery_id.is_(None),
                PostMetricSnapshot.bucket.in_(["7d", BACKFILL_BUCKET]),
            )
        )
        backfilled_deliveries = (
            db.query(PostMetricSnapshot.delivery_id)
            .filter(
                PostMetricSnapshot.delivery_id.isnot(None),
                PostMetricSnapshot.bucket.in_(["7d", BACKFILL_BUCKET]),
            )
        )

        needs_backfill = or_(
            PublishJob.deliveries.any(
                PublishDelivery.facebook_post_id.isnot(None)
                & ~PublishDelivery.id.in_(backfilled_deliveries)
            ),
            (
                ~PublishJob.deliveries.any(PublishDelivery.facebook_post_id.isnot(None))
                & ~PublishJob.id.in_(backfilled_legacy)
            ),
        )

        jobs = (
            db.query(PublishJob)
            .join(TargetFanpage)
            .options(
                joinedload(PublishJob.fanpage),
                joinedload(PublishJob.deliveries).joinedload(PublishDelivery.destination),
            )
            .filter(
                PublishJob.status == PublishJobStatus.published,
                PublishJob.is_deleted == False,
                PublishJob.scheduled_for < window_start,
                has_post_id_filter,
                needs_backfill,
            )
            .order_by(PublishJob.scheduled_for.desc())
            .limit(_BACKFILL_LIMIT * 4)
            .all()
        )

        job_ids = [j.id for j in jobs]
        if not job_ids:
            return

        backfilled_snapshots = (
            db.query(PostMetricSnapshot.publish_job_id, PostMetricSnapshot.delivery_id)
            .filter(
                PostMetricSnapshot.publish_job_id.in_(job_ids),
                PostMetricSnapshot.bucket.in_(["7d", BACKFILL_BUCKET]),
            )
            .all()
        )
        legacy_backfilled_jobs = {row[0] for row in backfilled_snapshots if row[1] is None}
        delivery_backfilled = {row[1] for row in backfilled_snapshots if row[1] is not None}

        due_list = []
        for job in jobs:
            targets = select_metric_targets(job)
            if not targets:
                continue

            age_mins = int((now - job.scheduled_for).total_seconds() // 60)
            for delivery_id, post_id, account_id in targets:
                if not post_id or not account_id:
                    continue
                if delivery_id is not None:
                    if delivery_id in delivery_backfilled:
                        continue
                else:
                    if job.id in legacy_backfilled_jobs:
                        continue

                due_list.append((job, delivery_id, post_id, account_id, BACKFILL_BUCKET, age_mins))
                if len(due_list) >= _BACKFILL_LIMIT:
                    break
            if len(due_list) >= _BACKFILL_LIMIT:
                break

        if not due_list:
            return

        _fetch_and_store(db, settings, due_list, now)
    finally:
        db.close()
