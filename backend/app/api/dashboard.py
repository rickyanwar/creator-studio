"""Dashboard overview stats endpoint."""

import shutil
from datetime import datetime, timezone

import pytz
from sqlalchemy import func
from fastapi import APIRouter

from app.api.deps import CurrentUser, DB
from app.config import get_settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
settings = get_settings()
WIB = pytz.timezone("Asia/Jakarta")


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


@router.get("/health")
def get_crawler_health(db: DB, _: CurrentUser):
    """Beat + crawler health: last crawl time, sleep window status, staleness."""
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource

    from app.models.settings import Settings as DBSettings

    now_utc = datetime.now(timezone.utc)
    now_wib = datetime.now(WIB)
    in_sleep = settings.crawl_sleep_start_wib <= now_wib.hour < settings.crawl_sleep_end_wib

    latest = db.query(func.max(IGSource.last_checked_at)).scalar()

    active_sources = (
        db.query(IGSource)
        .join(FanpageSource, FanpageSource.ig_source_id == IGSource.id)
        .filter(IGSource.is_active == True, FanpageSource.is_active == True)
        .distinct()
        .count()
    )

    # Read interval from DB so it matches what the user set in the UI
    db_settings = db.query(DBSettings).filter_by(id=1).first()
    crawl_interval = (
        db_settings.crawl_interval_minutes
        if db_settings and db_settings.crawl_interval_minutes
        else settings.crawl_interval_minutes
    )

    minutes_since = None
    beat_healthy = False

    if latest:
        latest_utc = latest.replace(tzinfo=timezone.utc) if latest.tzinfo is None else latest
        minutes_since = int((now_utc - latest_utc).total_seconds() / 60)
        stale_threshold = crawl_interval * 2
        beat_healthy = in_sleep or minutes_since <= stale_threshold

    return {
        "beat_healthy": beat_healthy,
        "last_crawl_at": latest.replace(tzinfo=timezone.utc).isoformat() if latest else None,
        "minutes_since_crawl": minutes_since,
        "in_sleep_window": in_sleep,
        "sleep_start_wib": settings.crawl_sleep_start_wib,
        "sleep_end_wib": settings.crawl_sleep_end_wib,
        "crawl_interval_minutes": crawl_interval,
        "server_time_utc": now_utc.isoformat(),
        "server_time_wib": now_wib.isoformat(),
        "active_sources": active_sources,
    }
