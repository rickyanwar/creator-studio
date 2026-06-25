from datetime import datetime, timedelta, timezone
from fastapi import APIRouter
from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def get_notifications(db: DB, _: CurrentUser):
    """Return active alerts from burners, publish failures, and rate limits."""
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    notifications = []
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)

    # ── Burner account issues ────────────────────────────────────────────
    burners = db.query(BurnerAccount).order_by(BurnerAccount.id).all()
    for b in burners:
        if b.status == BurnerStatus.challenged:
            notifications.append({
                "id": f"burner_{b.id}_challenged",
                "type": "warning",
                "title": f"@{b.ig_username} needs OTP",
                "message": "Instagram challenge — submit verification code",
                "link": "/burners",
                "created_at": _fmt(b.updated_at),
            })
        elif b.status == BurnerStatus.banned:
            notifications.append({
                "id": f"burner_{b.id}_banned",
                "type": "error",
                "title": f"@{b.ig_username} is banned",
                "message": "This burner account was banned by Instagram",
                "link": "/burners",
                "created_at": _fmt(b.updated_at),
            })
        elif b.status == BurnerStatus.rate_limited:
            until = ""
            if b.cooldown_until:
                cd = b.cooldown_until
                if cd.tzinfo is None:
                    cd = cd.replace(tzinfo=timezone.utc)
                mins = max(0, int((cd - now).total_seconds() / 60))
                until = f" — {mins}m remaining"
            notifications.append({
                "id": f"burner_{b.id}_rate_limited",
                "type": "warning",
                "title": f"@{b.ig_username} rate limited",
                "message": f"Cooling down{until}",
                "link": "/burners",
                "created_at": _fmt(b.updated_at),
            })
        elif b.last_error and b.status == BurnerStatus.active:
            notifications.append({
                "id": f"burner_{b.id}_error",
                "type": "error",
                "title": f"@{b.ig_username} has an error",
                "message": b.last_error[:120],
                "link": "/burners",
                "created_at": _fmt(b.updated_at),
            })

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

    # ── Burners with 0 sessions (no encrypted_session) ───────────────────
    for b in burners:
        if b.status == BurnerStatus.active and not b.encrypted_session:
            notifications.append({
                "id": f"burner_{b.id}_no_session",
                "type": "warning",
                "title": f"@{b.ig_username} has no session",
                "message": "Import a session to activate this burner",
                "link": "/burners",
                "created_at": _fmt(b.created_at),
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
