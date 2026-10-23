"""Dashboard overview stats endpoint."""

import os
import shutil
import zoneinfo
from datetime import datetime, timedelta, timezone

import pytz
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from fastapi import APIRouter

from app.api.deps import CurrentUser, DB
from app.config import get_settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
settings = get_settings()
WIB = pytz.timezone("Asia/Jakarta")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_local_midnight_utc(tz_name: str | None, now_utc: datetime) -> datetime:
    tz = None
    if tz_name:
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except Exception:
            try:
                tz = pytz.timezone(tz_name)
            except Exception:
                tz = None
    if tz is None:
        tz = zoneinfo.ZoneInfo("Europe/London")

    aware_now = now_utc if now_utc.tzinfo is not None else now_utc.replace(tzinfo=timezone.utc)
    local_now = aware_now.astimezone(tz)
    local_midnight = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight.astimezone(timezone.utc).replace(tzinfo=None)


def _to_naive_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _extract_first_url(val) -> str | None:
    if not val:
        return None
    if isinstance(val, (list, tuple)):
        # Handle SQLite ARRAY emulation where TEXT was split into single-char list
        if val and len(val) > 1 and all(isinstance(c, str) and len(c) == 1 for c in val):
            joined = "".join(val).strip()
            if joined.startswith("["):
                try:
                    import json
                    parsed = json.loads(joined)
                    if isinstance(parsed, list) and parsed:
                        return str(parsed[0])
                except Exception:
                    pass
            return joined
        return str(val[0]) if val else None
    if isinstance(val, str):
        val = val.strip()
        if val.startswith("["):
            try:
                import json
                parsed = json.loads(val)
                if isinstance(parsed, list) and parsed:
                    return str(parsed[0])
            except Exception:
                pass
        return val
    return None


@router.get("/stats")
def get_dashboard_stats(db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.target_fanpages import TargetFanpage
    from app.models.posts import Post
    from app.models.ai_copy_events import AICopyEvent
    from app.models.gallery_fetch_events import GalleryFetchEvent
    from app.models.social_publishing import SocialDestination

    now_utc = _utcnow()
    now_naive = _to_naive_utc(now_utc) or datetime.utcnow()
    today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)

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

    # Burner statuses (deprecated feature: return empty list for frontend compatibility)
    burner_stats = []

    # Active fanpages
    active_fps = (
        db.query(TargetFanpage)
        .filter(TargetFanpage.is_active == True)
        .order_by(TargetFanpage.name.asc())
        .all()
    )
    active_fanpages = len(active_fps)
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

    # Gallery folder size specifically
    disk_gallery_mb = 0
    try:
        gallery_path = os.path.join(media_path, "gallery")
        total_bytes = 0
        for dirpath, _dirnames, filenames in os.walk(gallery_path):
            for name in filenames:
                try:
                    total_bytes += os.path.getsize(os.path.join(dirpath, name))
                except OSError:
                    pass
        disk_gallery_mb = round(total_bytes / (1024 * 1024))
    except Exception:
        pass

    # AI copy-generation health (rolling 24h)
    ai_cutoff = now_naive - timedelta(hours=24)
    ai_window = db.query(AICopyEvent).filter(AICopyEvent.created_at >= ai_cutoff).all()
    ai_total = len(ai_window)
    ai_success = sum(1 for e in ai_window if e.outcome == "success")
    ai_recovered = sum(1 for e in ai_window if e.outcome == "recovered")
    ai_failed = sum(1 for e in ai_window if e.outcome == "failed")
    ai_stats = {
        "total": ai_total,
        "success": ai_success,
        "recovered": ai_recovered,
        "failed": ai_failed,
        "success_rate": round(100 * (ai_success + ai_recovered) / ai_total, 1) if ai_total else None,
    }

    # Gallery web/fetch (jina-reader) health today
    gallery_events_today = (
        db.query(GalleryFetchEvent)
        .filter(GalleryFetchEvent.created_at >= today_start, GalleryFetchEvent.context == "gallery")
        .all()
    )
    gallery_fetch_stats = {
        "total": len(gallery_events_today),
        "success": sum(1 for e in gallery_events_today if e.success),
        "failed": sum(1 for e in gallery_events_today if not e.success),
    }

    # ── TASK B5: brands_today ───────────────────────────────────────────
    fp_ids = [fp.id for fp in active_fps]
    brands_today = []
    if fp_ids:
        # Grouped query 1: active channels and daily_target per brand
        chan_rows = (
            db.query(
                SocialDestination.content_project_id,
                func.count(SocialDestination.id),
                func.coalesce(func.sum(SocialDestination.daily_target), 0),
            )
            .filter(
                SocialDestination.content_project_id.in_(fp_ids),
                SocialDestination.is_active == True,
            )
            .group_by(SocialDestination.content_project_id)
            .all()
        )
        channels_by_fp = {row[0]: (int(row[1]), int(row[2])) for row in chan_rows}

        # Grouped query 2: waiting_review (pending_review + pending_design + pending_publish)
        waiting_rows = (
            db.query(
                PublishJob.fanpage_id,
                func.count(PublishJob.id),
            )
            .filter(
                PublishJob.fanpage_id.in_(fp_ids),
                PublishJob.status.in_([
                    PublishJobStatus.pending_review,
                    PublishJobStatus.pending_design,
                    PublishJobStatus.pending_publish,
                ]),
                PublishJob.is_deleted == False,
            )
            .group_by(PublishJob.fanpage_id)
            .all()
        )
        waiting_by_fp = {row[0]: int(row[1]) for row in waiting_rows}

        # Grouped query 3: earliest future scheduled post
        next_rows = (
            db.query(
                PublishJob.fanpage_id,
                func.min(PublishJob.scheduled_for),
            )
            .filter(
                PublishJob.fanpage_id.in_(fp_ids),
                PublishJob.scheduled_for > now_naive,
                PublishJob.status != PublishJobStatus.failed,
                PublishJob.is_deleted == False,
            )
            .group_by(PublishJob.fanpage_id)
            .all()
        )
        next_by_fp = {row[0]: row[1] for row in next_rows}

        # Query 4: published & failed jobs across all active brands in recent window (last 48h)
        min_cutoff = now_naive - timedelta(hours=48)
        recent_published = (
            db.query(PublishJob.fanpage_id, PublishJob.published_at)
            .filter(
                PublishJob.fanpage_id.in_(fp_ids),
                PublishJob.status == PublishJobStatus.published,
                PublishJob.published_at >= min_cutoff,
                PublishJob.is_deleted == False,
            )
            .all()
        )
        recent_failed = (
            db.query(
                PublishJob.fanpage_id,
                func.coalesce(PublishJob.updated_at, PublishJob.created_at),
            )
            .filter(
                PublishJob.fanpage_id.in_(fp_ids),
                PublishJob.status == PublishJobStatus.failed,
                PublishJob.is_deleted == False,
                func.coalesce(PublishJob.updated_at, PublishJob.created_at) >= min_cutoff,
            )
            .all()
        )

        for fp in active_fps:
            midnight_utc = _get_local_midnight_utc(fp.timezone, now_utc)

            pub_count = sum(
                1 for row_fpid, pub_dt in recent_published
                if row_fpid == fp.id and _to_naive_utc(pub_dt) is not None and _to_naive_utc(pub_dt) >= midnight_utc
            )
            fail_count = sum(
                1 for row_fpid, fail_dt in recent_failed
                if row_fpid == fp.id and _to_naive_utc(fail_dt) is not None and _to_naive_utc(fail_dt) >= midnight_utc
            )

            active_chans, dest_target = channels_by_fp.get(fp.id, (0, 0))
            daily_target = dest_target if active_chans > 0 else int(fp.publish_daily_limit or 0)
            waiting = waiting_by_fp.get(fp.id, 0)
            next_post_dt = next_by_fp.get(fp.id)

            modes = []
            if getattr(fp, "mode1_ig_repost_enabled", False):
                modes.append("ig_repost")
            if getattr(fp, "mode2_news_content_enabled", False):
                modes.append("news_content")
            if getattr(fp, "ig_recreate_enabled", False):
                modes.append("ig_recreate")
            if getattr(fp, "discussion_enabled", False):
                modes.append("discussion")
            if getattr(fp, "pinterest_enabled", False):
                modes.append("pinterest_content")
            if getattr(fp, "facebook_photo_enabled", False):
                modes.append("facebook_recreate")
            if getattr(fp, "yt_clip_enabled", False):
                modes.append("youtube_clip")
            if getattr(fp, "ai_video_enabled", False):
                modes.append("ai_video")
            if getattr(fp, "radar_enabled", False):
                modes.append("viral_radar")

            initials = "".join([part[0].upper() for part in (fp.name or "").split()[:2]]) or "FP"

            brands_today.append({
                "fanpage_id": fp.id,
                "name": fp.name,
                "timezone": fp.timezone or "Europe/London",
                "published_today": pub_count,
                "waiting_review": waiting,
                "failed_today": fail_count,
                "active_channels": active_chans,
                "daily_target": daily_target,
                "modes": modes,
                "initials": initials,
                "next_post": next_post_dt.isoformat() if next_post_dt else None,
            })

    # ── TASK B5: latest_posts ───────────────────────────────────────────
    latest_jobs = (
        db.query(PublishJob)
        .options(joinedload(PublishJob.fanpage), joinedload(PublishJob.post))
        .filter(
            PublishJob.status == PublishJobStatus.published,
            PublishJob.is_deleted == False,
        )
        .order_by(PublishJob.published_at.desc(), PublishJob.id.desc())
        .limit(6)
        .all()
    )

    # When each post really goes live: the latest delivery slot (a Repliz/phone schedule), else the job's own.
    from sqlalchemy import func as _func
    from app.models.social_publishing import PublishDelivery
    go_live_by_job = dict(
        db.query(PublishDelivery.publish_job_id, _func.max(PublishDelivery.scheduled_for))
        .filter(PublishDelivery.publish_job_id.in_([j.id for j in latest_jobs] or [0]))
        .group_by(PublishDelivery.publish_job_id)
        .all()
    )

    latest_posts = []
    for job in latest_jobs:
        raw_title = (
            (job.design_title or "").strip()
            or (job.ai_generated_caption or "").strip()
            or ((job.post.original_caption if job.post else "") or "").strip()
        )
        title = raw_title[:80] if raw_title else ""
        content_type = (
            job.content_type.value
            if hasattr(job.content_type, "value")
            else str(job.content_type or "ig_repost")
        )
        thumb = (
            job.design_image_url
            or job.video_thumbnail_url
            or _extract_first_url(job.watermarked_image_urls)
            or (job.post and _extract_first_url(job.post.image_public_urls))
        )
        latest_posts.append({
            "id": job.id,
            "fanpage_id": job.fanpage_id,
            "fanpage_name": job.fanpage.name if job.fanpage else "",
            "content_type": content_type,
            "title": title,
            "published_at": job.published_at.isoformat() if job.published_at else None,
            "go_live_at": (go_live_by_job.get(job.id) or job.scheduled_for or job.published_at).isoformat()
            if (go_live_by_job.get(job.id) or job.scheduled_for or job.published_at) else None,
            "thumbnail_url": thumb,
        })

    return {
        "published_today": published_today,
        "failed_today": failed_today,
        "pending_review": pending_review,
        "active_fanpages": active_fanpages,
        "total_fanpages": total_fanpages,
        "burners": burner_stats,
        "disk_used_mb": disk_used_mb,
        "disk_total_mb": disk_total_mb,
        "disk_gallery_mb": disk_gallery_mb,
        "ai_stats": ai_stats,
        "gallery_fetch_stats": gallery_fetch_stats,
        "brands_today": brands_today,
        "latest_posts": latest_posts,
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
