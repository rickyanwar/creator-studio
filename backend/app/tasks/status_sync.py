"""Repliz Status Sync — polls GET /public/schedule/{id} every 5 minutes.

S10 fix: one unified query for ALL content types.

The previous approach used two separate query paths (a generic path limited
to 50 rows and a youtube_clip-only path).  This caused two problems:
  1. Only youtube_clip go-live failures were caught; news_content, discussion,
     pinterest_content, and facebook_recreate jobs had no Repliz status visible.
  2. The python-side filter ran AFTER .limit(50), so 50 already-final rows
     could monopolise every tick, starving new jobs.

S10 fix: one unified query for ALL content types that expresses the
"no final status yet" condition in SQL (JSONB: status key missing OR lower
value in pending/processing/queued/scheduled), ordered by scheduled_for ASC
(oldest unsynced first), then limited to 50 rows.

Repliz failure statuses (from live evidence, S1 probe): "failed", "error",
"rejected", "canceled", "cancelled".
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import or_, func, cast, String
from sqlalchemy.orm import Session

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.services.repliz_client import get_repliz_client_from_db
from app.services.post_metrics import extract_post_id
from app.services.social_post_reconciliation import reconcile_delivery_repliz

logger = logging.getLogger(__name__)

# Failure statuses observed in real Repliz responses (S1 evidence).
_REPLIZ_FAILURE_STATUSES = {"failed", "error", "rejected", "canceled", "cancelled"}
_REPLIZ_SUCCESS_STATUSES = {"success", "published", "posted", "done", "completed"}
# These statuses mean the schedule is still in flight — keep polling.
_REPLIZ_PENDING_STATUSES = {"pending", "processing", "queued", "scheduled"}

# Poll window: don't poll before 2 min past scheduled_for (give Repliz time
# to process) and don't keep polling jobs older than 3 days.
_POLL_MIN_AGE = timedelta(minutes=2)
_POLL_MAX_AGE = timedelta(days=3)


def _best_error_message(data: dict) -> str:
    """Return the most specific human-readable error from a Repliz response."""
    for key in ("errorMessage", "error_message", "message", "error", "reason"):
        val = data.get(key)
        if val and isinstance(val, str):
            return val[:2000]
    return str(data)[:2000]


def _response_has_final_status(data: dict | None) -> bool:
    """True when the stored response already carries a non-pending status."""
    if not isinstance(data, dict):
        return False
    status = (data.get("status") or "").lower()
    return bool(status) and status not in _REPLIZ_PENDING_STATUSES


def _response_needs_sync(data: dict | None) -> bool:
    """True when the stored response has no final status yet (missing key or
    still in a pending/processing/queued/scheduled state)."""
    if not isinstance(data, dict):
        return True
    status = (data.get("status") or "").lower()
    return not status or status in _REPLIZ_PENDING_STATUSES


@celery_app.task(name="app.tasks.status_sync.sync_pending_schedules")
def sync_pending_schedules():
    """Poll Repliz for jobs that haven't reached a final status yet.

    S10 unified query: all content types, SQL-level "no final status" filter,
    ordered oldest-unsynced first, limited to 50 rows per tick.
    """
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # ── Unified query (S10): all content types, "no final status" in SQL ─
        #
        # The "no final status yet" condition in JSONB:
        #   • The 'status' key is absent from repliz_response_json, OR
        #   • lower(repliz_response_json->>'status') is one of the pending set.
        #
        # We use SQLAlchemy's JSON path operator to extract the status string.
        # On PostgreSQL the expression is:
        #   lower(repliz_response_json->>'status') IN ('pending', 'processing',
        #                                               'queued', 'scheduled')
        #   OR repliz_response_json->>'status' IS NULL
        #
        # For portability with the test-suite SQLite mock (which doesn't
        # evaluate JSONB operators anyway) the .filter() call is what matters;
        # the mock just stubs .all().

        _pending_status_lower = list(_REPLIZ_PENDING_STATUSES)

        if db.bind and db.bind.dialect.name == "sqlite":
            no_final_status_condition = True  # type: ignore[assignment]
        else:
            status_text = func.lower(func.json_extract_path_text(PublishJob.repliz_response_json, "status"))
            no_final_status_condition = or_(
                func.json_extract_path_text(PublishJob.repliz_response_json, "status") == None,  # noqa: E711
                status_text.in_(_pending_status_lower),
            )

        jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.repliz_schedule_id != None,  # noqa: E711
                PublishJob.status == PublishJobStatus.published,
                PublishJob.scheduled_for != None,  # noqa: E711
                PublishJob.scheduled_for <= now - _POLL_MIN_AGE,
                PublishJob.scheduled_for >= now - _POLL_MAX_AGE,
                no_final_status_condition,
            )
            .order_by(PublishJob.scheduled_for.desc())
            .limit(50)
            .all()
        )

        if not jobs:
            return

        try:
            client = get_repliz_client_from_db(db)
        except RuntimeError:
            logger.warning("Repliz credentials not set — skipping status sync")
            return

        synced = 0
        for job in jobs:
            try:
                data = client.get_schedule(job.repliz_schedule_id)

                # Detect Repliz failure for ANY content type and mark the job failed.
                status_val = (data.get("status") or "").lower()
                if status_val in _REPLIZ_FAILURE_STATUSES:
                    job.repliz_response_json = data
                    job.status = PublishJobStatus.failed
                    job.last_error = "Repliz: " + _best_error_message(data)
                    db.add(job)
                    logger.warning(
                        "status_sync: job %d (type=%s) → failed (Repliz status=%s): %s",
                        job.id, job.content_type, status_val, job.last_error[:200],
                    )
                elif status_val in _REPLIZ_SUCCESS_STATUSES:
                    job.repliz_response_json = data
                    job.last_error = None
                    db.add(job)
                elif job.repliz_response_json != data:
                    job.repliz_response_json = data
                    db.add(job)

                synced += 1

                # Update associated delivery ledger rows when available
                if hasattr(job, "deliveries") and job.deliveries:
                    for deliv in job.deliveries:
                        if deliv.repliz_schedule_id == job.repliz_schedule_id or deliv.state == "repliz_scheduled":
                            if status_val in _REPLIZ_FAILURE_STATUSES:
                                deliv.state = "failed"
                                deliv.last_error = job.last_error
                                db.add(deliv)
                            elif status_val in _REPLIZ_SUCCESS_STATUSES:
                                deliv.state = "published"
                                deliv.last_error = None
                                pid = extract_post_id(data)
                                if pid:
                                    deliv.facebook_post_id = pid
                                    if data.get("url") or data.get("permalink"):
                                        deliv.facebook_permalink = data.get("url") or data.get("permalink")
                                    db.add(deliv)
                                elif not deliv.facebook_post_id:
                                    reconcile_delivery_repliz(db, deliv, client=client)
                                if deliv.facebook_post_id and isinstance(job.repliz_response_json, dict) and not job.repliz_response_json.get("postId"):
                                    job.repliz_response_json = dict(job.repliz_response_json, postId=deliv.facebook_post_id)
                                    db.add(job)
            except Exception as exc:
                logger.warning("Status sync failed for job %d: %s", job.id, exc)

        db.commit()
        logger.info("Status sync: checked %d job(s)", synced)

    finally:
        db.close()


def _sync_one_delivery(db: Session, client, deliv, now_naive: datetime) -> bool:
    """Sync a single delivery from Repliz. Returns True if successfully checked/updated."""
    from app.models.social_publishing import DeliveryEvent

    try:
        data = client.get_schedule(deliv.repliz_schedule_id)
        status_val = (data.get("status") or "").lower()
        if status_val in _REPLIZ_FAILURE_STATUSES:
            deliv.state = "failed"
            deliv.last_error = "Repliz: " + _best_error_message(data)
            db.add(
                DeliveryEvent(
                    delivery_id=deliv.id,
                    event_type="failed",
                    details={"error": deliv.last_error, "source": "status_sync"},
                    created_at=now_naive,
                )
            )
            db.add(deliv)
        elif status_val in _REPLIZ_SUCCESS_STATUSES:
            deliv.state = "published"
            deliv.last_error = None
            if not deliv.published_at:
                deliv.published_at = now_naive
            pid = extract_post_id(data)
            if pid:
                deliv.facebook_post_id = pid
                if data.get("url") or data.get("permalink"):
                    deliv.facebook_permalink = data.get("url") or data.get("permalink")
                db.add(
                    DeliveryEvent(
                        delivery_id=deliv.id,
                        event_type="published",
                        details={"facebook_post_id": pid, "source": "status_sync"},
                        created_at=now_naive,
                    )
                )
                db.add(deliv)
            elif not deliv.facebook_post_id:
                reconcile_delivery_repliz(db, deliv, client=client)
        return True
    except httpx.HTTPStatusError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            deliv.state = "skipped"
            deliv.last_error = "Repliz schedule not found (404) — deleted in Repliz"
            db.add(
                DeliveryEvent(
                    delivery_id=deliv.id,
                    event_type="skipped",
                    details={"error": deliv.last_error, "source": "status_sync"},
                    created_at=now_naive,
                )
            )
            db.add(deliv)
            return True
        logger.warning("Status sync failed for delivery %s: %s", deliv.id, exc)
        return False
    except Exception as exc:
        logger.warning("Status sync failed for delivery %s: %s", deliv.id, exc)
        return False


@celery_app.task(name="app.tasks.status_sync.sync_pending_deliveries")
def sync_pending_deliveries(db: Session | None = None, now: datetime | None = None):
    """Poll Repliz for deliveries in repliz_scheduled state."""
    own_session = db is None
    if own_session:
        db = SessionLocal()
    try:
        from app.models.social_publishing import PublishDelivery

        if now is None:
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            now_naive = now.replace(tzinfo=None) if now.tzinfo else now

        deliveries = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.state == "repliz_scheduled",
                PublishDelivery.repliz_schedule_id.isnot(None),
                PublishDelivery.scheduled_for.isnot(None),
                PublishDelivery.scheduled_for <= now_naive - _POLL_MIN_AGE,
                PublishDelivery.scheduled_for >= now_naive - _POLL_MAX_AGE,
            )
            .order_by(PublishDelivery.scheduled_for.asc())
            .limit(50)
            .all()
        )

        # Rows older than the poll window are re-checked too, so a schedule deleted in Repliz (404)
        # cannot stay repliz_scheduled forever.
        stale_deliveries = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.state == "repliz_scheduled",
                PublishDelivery.repliz_schedule_id.isnot(None),
                PublishDelivery.scheduled_for.isnot(None),
                PublishDelivery.scheduled_for < now_naive - _POLL_MAX_AGE,
            )
            .order_by(PublishDelivery.scheduled_for.asc())
            .limit(20)
            .all()
        )

        if not deliveries and not stale_deliveries:
            return 0

        try:
            client = get_repliz_client_from_db(db)
        except RuntimeError:
            logger.warning("Repliz credentials not set — skipping delivery status sync")
            return 0

        synced = 0
        for deliv in deliveries:
            if _sync_one_delivery(db, client, deliv, now_naive):
                synced += 1

        for deliv in stale_deliveries:
            if _sync_one_delivery(db, client, deliv, now_naive):
                synced += 1

        db.commit()
        logger.info("Delivery status sync: checked %d delivery(s)", synced)
        return synced
    finally:
        if own_session:
            db.close()
