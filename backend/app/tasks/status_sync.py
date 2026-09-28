"""Repliz Status Sync — polls GET /public/schedule/{id} every 5 minutes.

Two query paths run each tick:

  1. Generic — existing behaviour: jobs with status=published AND a stored
     repliz_response_json whose "status" key is in processing/pending.

  2. Mode 7 clip path (S2a fix): youtube_clip jobs with status=published,
     a repliz_schedule_id set, scheduled_for in the [now-3days, now-2min]
     window, whose stored response has NO final status (the key is missing
     or still in pending/processing/queued/scheduled). The create response
     is just {"scheduleId": …}, so status_key never reaches processing/pending
     on the first sync, leaving go-live failures invisible. This path catches
     them.

Repliz failure statuses (from live evidence, S1 probe): "failed", "error",
"rejected", "canceled", "cancelled".
"""

import logging
from datetime import datetime, timedelta, timezone

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.services.repliz_client import get_repliz_client_from_db

logger = logging.getLogger(__name__)

# Failure statuses observed in real Repliz responses (S1 evidence).
_REPLIZ_FAILURE_STATUSES = {"failed", "error", "rejected", "canceled", "cancelled"}
# These statuses mean the schedule is still in flight — keep polling.
_REPLIZ_PENDING_STATUSES = {"pending", "processing", "queued", "scheduled"}

# Clip window: don't poll before 2 min past scheduled_for (give Repliz time
# to process) and don't keep polling jobs older than 3 days.
_CLIP_POLL_MIN_AGE = timedelta(minutes=2)
_CLIP_POLL_MAX_AGE = timedelta(days=3)


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
    """Poll Repliz for jobs that haven't reached a final status yet."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # ── Path 1: existing behaviour — any job whose stored response
        # already has a pending/processing status key. ────────────────────────
        generic_jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.repliz_schedule_id != None,  # noqa: E711
                PublishJob.status == PublishJobStatus.published,
                PublishJob.repliz_response_json["status"].astext.in_(["processing", "pending"])
                if hasattr(PublishJob.repliz_response_json, "astext")
                else True,
            )
            .limit(50)
            .all()
        )

        # ── Path 2 (S2a): youtube_clip jobs in the polling window whose
        # stored response has no final status (the common case for new clips
        # because the create response is just {"scheduleId": …}). ────────────
        clip_jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.content_type == ContentType.youtube_clip,
                PublishJob.status == PublishJobStatus.published,
                PublishJob.repliz_schedule_id != None,  # noqa: E711
                PublishJob.scheduled_for != None,  # noqa: E711
                # Not too fresh (Repliz needs time to process the go-live)
                PublishJob.scheduled_for <= now - _CLIP_POLL_MIN_AGE,
                # Not too old (stop burning API quota on ancient clips)
                PublishJob.scheduled_for >= now - _CLIP_POLL_MAX_AGE,
            )
            .limit(50)
            .all()
        )
        # Keep only clip jobs whose stored response still needs a status update,
        # and skip any that are already in generic_jobs (avoid double-polling).
        generic_ids = {j.id for j in generic_jobs}
        clip_jobs = [
            j for j in clip_jobs
            if j.id not in generic_ids and _response_needs_sync(j.repliz_response_json)
        ]

        jobs = generic_jobs + clip_jobs
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

                # For Mode 7 clips: detect failure and mark the job failed.
                status_val = (data.get("status") or "").lower()
                if (
                    job.content_type == ContentType.youtube_clip
                    and status_val in _REPLIZ_FAILURE_STATUSES
                ):
                    job.repliz_response_json = data
                    job.status = PublishJobStatus.failed
                    job.last_error = "Repliz: " + _best_error_message(data)
                    db.add(job)
                    logger.warning(
                        "status_sync: clip job %d → failed (Repliz status=%s): %s",
                        job.id, status_val, job.last_error[:200],
                    )
                elif job.repliz_response_json != data:
                    job.repliz_response_json = data
                    db.add(job)

                synced += 1
            except Exception as exc:
                logger.warning("Status sync failed for job %d: %s", job.id, exc)

        db.commit()
        logger.info(
            "Status sync: checked %d job(s) (%d generic + %d clip)",
            synced, len(generic_jobs), len(clip_jobs),
        )

    finally:
        db.close()
