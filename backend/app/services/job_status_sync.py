"""Derive parent job status from its delivery ledger without database access.
All accepted deliveries count as published, including Repliz schedules.
Finished deliveries count as published if any destination succeeded.
Otherwise failures take priority over skips, with lowest-id error selection.
Unfinished or uncertain deliveries leave the parent status unchanged.
Publication timestamps retain delivery history and media survive go-live.
Published jobs are never downgraded or timestamped again.
"""

from datetime import datetime, timedelta, timezone

from app.models.publish_jobs import PublishJobStatus

ACCEPTED_DELIVERY_STATES = ("repliz_scheduled", "published")
TERMINAL_DELIVERY_STATES = ("published", "failed", "skipped")
SYNCABLE_JOB_STATUSES = (PublishJobStatus.pending_publish, PublishJobStatus.publishing)


def decide_job_status(deliveries) -> tuple[PublishJobStatus | None, str | None]:
    if not deliveries:
        return None, None
    if all(d.state in ACCEPTED_DELIVERY_STATES for d in deliveries):
        return PublishJobStatus.published, None
    if all(d.state == "skipped" and (d.last_error or "").startswith("stale:") for d in deliveries):
        return PublishJobStatus.skipped, min(deliveries, key=lambda d: d.id).last_error
    if not all(d.state in TERMINAL_DELIVERY_STATES for d in deliveries):
        return None, None
    if any(d.state == "published" for d in deliveries):
        return PublishJobStatus.published, None
    failed = [d for d in deliveries if d.state == "failed"]
    delivery = min(failed or deliveries, key=lambda d: d.id)
    state = "failed" if failed else "skipped"
    status = PublishJobStatus.failed if failed else PublishJobStatus.skipped
    return status, f"delivery {delivery.id} {state}: {delivery.last_error or 'no error text'}"


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def apply_job_status(job, deliveries, now_naive) -> bool:
    if job.status not in (*SYNCABLE_JOB_STATUSES, PublishJobStatus.published):
        return False
    status, err = decide_job_status(deliveries)
    if status is None or job.status == PublishJobStatus.published:
        return False
    if status == PublishJobStatus.published:
        job.status = status
        job.last_error = None
        published_times = [
            _naive_utc(d.published_at) for d in deliveries
            if d.state in ACCEPTED_DELIVERY_STATES and d.published_at is not None
        ]
        job.published_at = max(published_times) if published_times else now_naive
        go_lives = [_naive_utc(d.scheduled_for) for d in deliveries if d.scheduled_for is not None]
        job.cleanup_at = max([job.published_at, *go_lives]) + timedelta(hours=6)
        if len(deliveries) == 1 and deliveries[0].actual_method == "repliz":
            job.repliz_schedule_id = deliveries[0].repliz_schedule_id
            job.scheduled_for = deliveries[0].scheduled_for
        return True
    if job.status == status and job.last_error == err:
        return False
    job.status = status
    job.last_error = err
    return True
