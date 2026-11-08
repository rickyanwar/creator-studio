"""Group-only production budget, hand-over and media retention."""

from datetime import datetime, timedelta, timezone
import logging
import re

from sqlalchemy import select

from app.models.fb_group_share import FbShareGroupJob, FbShareItem, FbShareTask
from app.models.publish_jobs import PublishJob, PublishJobStatus
from .brand_settings import get_brand_share

logger = logging.getLogger(__name__)


def _utc_naive(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def mark_group_job(db, *, fanpage_id: int, mode: str, publish_job_id: int | None = None, video_job_id: int | None = None) -> None:
    if publish_job_id is None and video_job_id is None:
        raise ValueError("A publish_job_id or video_job_id is required")
    db.add(FbShareGroupJob(fanpage_id=fanpage_id, mode=mode,
                           publish_job_id=publish_job_id, video_job_id=video_job_id))


def link_video_publish_job(db, *, video_job_id: int, publish_job_id: int) -> None:
    row = db.query(FbShareGroupJob).filter_by(video_job_id=video_job_id).first()
    if row is not None:
        row.publish_job_id = publish_job_id


def is_group_only(db, publish_job_id: int) -> bool:
    return db.query(FbShareGroupJob.id).filter_by(publish_job_id=publish_job_id).first() is not None


def is_group_only_video(db, video_job_id: int) -> bool:
    return db.query(FbShareGroupJob.id).filter_by(video_job_id=video_job_id).first() is not None


def group_publish_job_ids():
    return select(FbShareGroupJob.publish_job_id).where(FbShareGroupJob.publish_job_id.isnot(None))


def group_video_job_ids():
    return select(FbShareGroupJob.video_job_id).where(FbShareGroupJob.video_job_id.isnot(None))


def remaining_groups_only_today(db, fanpage_id: int, mode: str, now: datetime) -> int:
    enabled, settings = get_brand_share(db, fanpage_id)
    source = settings["sources"].get(mode, {})
    if not enabled or source.get("source") not in ("groups_only", "both"):
        return 0
    start = _utc_naive(now).replace(hour=0, minute=0, second=0, microsecond=0)
    used = db.query(FbShareGroupJob).filter(
        FbShareGroupJob.fanpage_id == fanpage_id, FbShareGroupJob.mode == mode,
        FbShareGroupJob.created_at >= start,
        FbShareGroupJob.created_at < start + timedelta(days=1),
    ).count()
    return max(0, source.get("groups_only_per_day", 0) - used)


def hand_over(db, job: PublishJob, now) -> FbShareItem | None:
    from app.services.social_delivery_planner import build_delivery_payload
    from .feeder import MEDIA_KINDS, load_image_bytes
    from app.services.radar_clustering import compute_phash

    row = db.query(FbShareGroupJob).filter_by(publish_job_id=job.id).with_for_update().first()
    if row is None:
        return None
    item = db.query(FbShareItem).filter_by(
        fanpage_id=job.fanpage_id, source_kind="publish_job", source_ref=str(job.id),
    ).first()
    if item is not None and row.handed_at is not None:
        return item
    if job.status not in (PublishJobStatus.pending_publish, PublishJobStatus.pending_review) or job.last_error:
        return None
    now = _utc_naive(now)
    if item is None:
        _, settings = get_brand_share(db, job.fanpage_id)
        niches = settings["niches"]
        if not niches:
            return None
        try:
            payload = build_delivery_payload(job)
        except ValueError:
            return None
        phash = None
        if payload.media_kind != "video":
            try:
                phash = compute_phash(load_image_bytes(job.design_image_path or payload.media_refs[0]))
            except Exception:
                logger.warning("Group Share: skipping publish job %s: first media phash unavailable", job.id)
                return None
        caption = payload.caption or ""
        match = re.search(r"(?:\s|^)(#\w+(?:\s+#\w+)*)\s*$", caption)
        item = FbShareItem(
            fanpage_id=job.fanpage_id, niche=niches[0] if len(niches) == 1 else "",
            source_kind="publish_job", source_ref=str(job.id),
            media_kind=MEDIA_KINDS[payload.media_kind], media=list(payload.media_refs),
            caption=caption[:match.start()].rstrip() if match else caption,
            hashtags=" ".join(match.group(1).split()) if match else "", phash=phash,
            status="new", created_at=now,
            expires_at=now + timedelta(hours=12 if row.mode == "news" else 72),
        )
        db.add(item)
    row.handed_at = row.handed_at or now
    job.status = PublishJobStatus.skipped
    job.last_error = "group_only: handed to Group Share"
    db.flush()
    return item


def media_cleanup_allowed(db, publish_job_id: int, now: datetime) -> bool:
    """Keep group media until every share has finished, plus seven days."""
    row = db.query(FbShareGroupJob).filter_by(publish_job_id=publish_job_id).first()
    if row is None:
        return True
    item = db.query(FbShareItem).filter_by(
        fanpage_id=row.fanpage_id, source_kind="publish_job", source_ref=str(publish_job_id),
    ).first()
    if item is None or item.status not in ("done", "expired", "skipped"):
        return False
    tasks = db.query(FbShareTask).filter_by(item_id=item.id).all()
    if any(task.state in ("planned", "sent_to_pic", "publishing", "pending_admin", "uncertain") for task in tasks):
        return False
    # ponytail: items have no finished_at; retain through expiry and task completion.
    finished = [task.updated_at for task in tasks if task.updated_at]
    if item.expires_at:
        finished.append(item.expires_at)
    return bool(finished) and max(_utc_naive(value) for value in finished) + timedelta(days=7) <= _utc_naive(now)
