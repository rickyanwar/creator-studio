from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query
from typing import Optional
from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
def get_logs(
    db: DB,
    _: CurrentUser,
    category: Optional[str] = Query(None),  # burner | publish | all
    days: int = Query(30, le=90),
):
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.target_fanpages import TargetFanpage

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    items = []

    # ── Burner account issues ────────────────────────────────────────────────
    if category in (None, "burner"):
        burners = db.query(BurnerAccount).all()
        for b in burners:
            updated = b.updated_at or now
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)

            if b.status == BurnerStatus.challenged:
                items.append({
                    "id": f"burner_challenged_{b.id}",
                    "category": "burner",
                    "type": "challenge",
                    "severity": "warning",
                    "title": f"@{b.ig_username} — OTP required",
                    "message": b.last_error or "Instagram is challenging this account. Submit OTP to restore access.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })
            elif b.status == BurnerStatus.banned:
                items.append({
                    "id": f"burner_banned_{b.id}",
                    "category": "burner",
                    "type": "ban",
                    "severity": "error",
                    "title": f"@{b.ig_username} — Account banned",
                    "message": b.last_error or "This burner account has been banned by Instagram.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })
            elif b.status == BurnerStatus.rate_limited:
                items.append({
                    "id": f"burner_rate_{b.id}",
                    "category": "burner",
                    "type": "rate_limit",
                    "severity": "warning",
                    "title": f"@{b.ig_username} — Rate limited",
                    "message": b.last_error or "Account hit Instagram request limits. Will auto-reset tomorrow.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })
            elif b.last_error and b.status == BurnerStatus.active:
                items.append({
                    "id": f"burner_error_{b.id}",
                    "category": "burner",
                    "type": "session_error",
                    "severity": "error",
                    "title": f"@{b.ig_username} — Session error",
                    "message": b.last_error,
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })

            # No session at all
            if not b.encrypted_session and b.status == BurnerStatus.active:
                items.append({
                    "id": f"burner_nosession_{b.id}",
                    "category": "burner",
                    "type": "no_session",
                    "severity": "warning",
                    "title": f"@{b.ig_username} — No session",
                    "message": "No Instagram session imported. Crawling won't work until a session is set.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })

    # ── Failed publish jobs ──────────────────────────────────────────────────
    if category in (None, "publish"):
        failed_jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.status == PublishJobStatus.failed,
                PublishJob.updated_at >= cutoff,
            )
            .order_by(PublishJob.updated_at.desc())
            .limit(100)
            .all()
        )
        for job in failed_jobs:
            fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
            fanpage_name = fanpage.name if fanpage else f"Fanpage #{job.fanpage_id}"
            updated = job.updated_at or now
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)

            items.append({
                "id": f"job_failed_{job.id}",
                "category": "publish",
                "type": "publish_failed",
                "severity": "error",
                "title": f"Post failed — {fanpage_name}",
                "message": job.last_error or "Publish job failed without an error message.",
                "account": fanpage_name,
                "occurred_at": updated.isoformat(),
                "link": "/history",
            })

    # Sort newest first
    items.sort(key=lambda x: x["occurred_at"], reverse=True)

    error_count   = sum(1 for i in items if i["severity"] == "error")
    warning_count = sum(1 for i in items if i["severity"] == "warning")

    return {
        "logs": items,
        "total": len(items),
        "error_count": error_count,
        "warning_count": warning_count,
    }
