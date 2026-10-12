from datetime import datetime, timedelta, timezone
from fastapi import APIRouter
from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def get_notifications(db: DB, _: CurrentUser):
    """Return active alerts from publish failures."""
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    notifications = []
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)

    # ── Failed publish jobs (last 24 h) ──────────────────────────────────
    failed_jobs = (
        db.query(PublishJob)
        .filter(
            PublishJob.status == PublishJobStatus.failed,
            PublishJob.updated_at >= since_24h,
        )
        .order_by(PublishJob.updated_at.desc())
        .limit(5)
        .all()
    )
    for job in failed_jobs:
        notifications.append({
            "id": f"job_{job.id}_failed",
            "type": "error",
            "title": "Post failed to publish",
            "message": (job.last_error or "Unknown error")[:120],
            "link": "/queue",
            "created_at": _fmt(job.updated_at),
        })

    # Sort: errors first, then warnings, then by time desc
    order = {"error": 0, "warning": 1, "info": 2}
    notifications.sort(key=lambda n: (order.get(n["type"], 9), n["created_at"]), reverse=False)

    return {"notifications": notifications, "unread": len(notifications)}


def _fmt(dt) -> str:
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()
