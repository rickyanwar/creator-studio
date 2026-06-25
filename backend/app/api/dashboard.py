"""Dashboard overview stats endpoint."""

import os
import shutil
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter

from app.api.deps import CurrentUser, DB
from app.config import get_settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
settings = get_settings()


@router.get("/stats")
def get_dashboard_stats(db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.models.target_fanpages import TargetFanpage
    from app.models.posts import Post

    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)

    # Published today
    published_today = (
        db.query(PublishJob)
        .filter(PublishJob.published_at >= today_start, PublishJob.status == PublishJobStatus.published)
        .count()
    )

    # Failed today
    failed_today = (
        db.query(PublishJob)
        .filter(PublishJob.updated_at >= today_start, PublishJob.status == PublishJobStatus.failed)
        .count()
    )

    # Pending review
    pending_review = (
        db.query(PublishJob).filter(PublishJob.status == PublishJobStatus.pending_review).count()
    )

    # Burner statuses
    burners = db.query(BurnerAccount).all()
    burner_stats = [
        {
            "id": b.id,
            "ig_username": b.ig_username,
            "status": b.status,
            "requests_today": b.requests_today,
            "cooldown_until": b.cooldown_until,
            "last_error": b.last_error,
        }
        for b in burners
    ]

    # Active fanpages
    active_fanpages = db.query(TargetFanpage).filter_by(is_active=True).count()
    total_fanpages = db.query(TargetFanpage).count()

    # Disk usage
    media_path = settings.storage_base_path
    disk_used_mb = 0
    disk_total_mb = 0
    try:
        usage = shutil.disk_usage(media_path)
        disk_used_mb = round(usage.used / (1024 * 1024))
        disk_total_mb = round(usage.total / (1024 * 1024))
    except Exception:
        pass

    return {
        "published_today": published_today,
        "failed_today": failed_today,
        "pending_review": pending_review,
        "active_fanpages": active_fanpages,
        "total_fanpages": total_fanpages,
        "burners": burner_stats,
        "disk_used_mb": disk_used_mb,
        "disk_total_mb": disk_total_mb,
    }
