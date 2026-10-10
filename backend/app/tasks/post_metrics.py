import logging
import time
from datetime import datetime, timezone, timedelta

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload
from sqlalchemy.dialects.postgresql import insert

from app.database import SessionLocal
from app.models.settings import Settings
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.target_fanpages import TargetFanpage
from app.services.post_metrics import (
    TRACK_WINDOW_MINUTES,
    BACKFILL_BUCKET,
    due_bucket,
    parse_statistic,
    extract_post_id,
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

    for i, (job, post_id, bucket, age_mins) in enumerate(due_list):
        if i > 0:
            time.sleep(_CALL_PAUSE_SECONDS)

        try:
            raw_json = client.get_content_statistic(post_id, job.fanpage.repliz_account_id)
            counts = parse_statistic(raw_json)

            stmt = insert(PostMetricSnapshot).values(
                publish_job_id=job.id,
                fanpage_id=job.fanpage_id,
                bucket=bucket,
                captured_at=now,
                age_minutes=age_mins,
                likes=counts.likes,
                comments=counts.comments,
                shares=counts.shares,
                raw_json=raw_json
            ).on_conflict_do_nothing(index_elements=["publish_job_id", "bucket"])
            
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

        jobs = (
            db.query(PublishJob)
            .join(TargetFanpage)
            .options(joinedload(PublishJob.fanpage))
            .filter(
                PublishJob.status == PublishJobStatus.published,
                PublishJob.is_deleted == False,
                PublishJob.scheduled_for >= window_start,
                PublishJob.scheduled_for <= window_end,
                func.json_extract_path_text(PublishJob.repliz_response_json, "postId").isnot(None)
            )
            .order_by(PublishJob.scheduled_for.desc())
            .all()
        )

        job_ids = [j.id for j in jobs]
        if not job_ids:
            return

        snapshots = (
            db.query(PostMetricSnapshot.publish_job_id, PostMetricSnapshot.bucket)
            .filter(PostMetricSnapshot.publish_job_id.in_(job_ids))
            .all()
        )

        taken_map = {}
        for jid, b in snapshots:
            taken_map.setdefault(jid, set()).add(b)

        due_list = []
        for job in jobs:
            post_id = extract_post_id(job.repliz_response_json)
            if not post_id:
                continue

            age_mins = int((now - job.scheduled_for).total_seconds() // 60)
            taken = taken_map.get(job.id, set())
            bucket = due_bucket(age_mins, taken)
            if bucket:
                due_list.append((job, post_id, bucket, age_mins))

        due_list = due_list[:_COLLECT_LIMIT]
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

        jobs = (
            db.query(PublishJob)
            .join(TargetFanpage)
            .options(joinedload(PublishJob.fanpage))
            .filter(
                PublishJob.status == PublishJobStatus.published,
                PublishJob.is_deleted == False,
                PublishJob.scheduled_for < window_start,
                ~PublishJob.id.in_(
                    db.query(PostMetricSnapshot.publish_job_id)
                    .filter(PostMetricSnapshot.bucket.in_(["7d", BACKFILL_BUCKET]))
                ),
                func.json_extract_path_text(PublishJob.repliz_response_json, "postId").isnot(None)
            )
            .order_by(PublishJob.scheduled_for.desc())
            .limit(_BACKFILL_LIMIT * 4)
            .all()
        )

        due_list = []
        for job in jobs:
            post_id = extract_post_id(job.repliz_response_json)
            if not post_id:
                continue

            age_mins = int((now - job.scheduled_for).total_seconds() // 60)
            due_list.append((job, post_id, BACKFILL_BUCKET, age_mins))
            if len(due_list) >= _BACKFILL_LIMIT:
                break

        if not due_list:
            return

        _fetch_and_store(db, settings, due_list, now)
    finally:
        db.close()
