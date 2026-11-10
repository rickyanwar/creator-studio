"""Group-only production budget, hand-over and media retention."""

from datetime import datetime, timedelta, timezone
import logging
import re

from sqlalchemy import select

from app.models.fb_group_share import FbShareGroupJob, FbShareItem, FbShareTask
from app.models.publish_jobs import PublishJob, PublishJobStatus
from .brand_settings import get_brand_share
from app.services.muse_labeler import redis_client

logger = logging.getLogger(__name__)


def _utc_naive(now: datetime) -> datetime:
    return now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now


def mark_group_job(db, *, fanpage_id: int, mode: str, publish_job_id: int | None = None, video_job_id: int | None = None) -> None:
    if publish_job_id is None and video_job_id is None:
        raise ValueError("A publish_job_id or video_job_id is required")
    # Lane jobs are marked by the lanes.after_flush listener; never mark a job twice.
    key = FbShareGroupJob.publish_job_id == publish_job_id if publish_job_id is not None else (
        FbShareGroupJob.video_job_id == video_job_id)
    if db.query(FbShareGroupJob.id).filter(key).first() is not None:
        return
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


# Modes whose lane has no own daily-count column; their cap is the brand's
# sources[mode].groups_only_per_day (0 = no group posts of that mode).
LANE_CAPPED_MODES = ("ig", "news")


# Lane reservations: one Redis key per reservation, so a worker that dies between reserve and
# job creation (SIGKILL, OOM, container recreate) only holds its slot until the key expires.
LANE_RESERVATION_PREFIX = "fb_share:lane_res:"
LANE_RESERVATION_TTL_SECONDS = 30 * 60   # longest legit gap from reserve to job creation
LANE_LOCK_PREFIX = "fb_share:lane_res_lock:"
LANE_LOCK_TTL_MS = 5000
LANE_LOCK_WAIT_SECONDS = 6.0             # > lock TTL, so a dead holder's lock always expires first
LANE_LOCK_RETRY_SECONDS = 0.05


def _brand_local_date(brand, now: datetime):
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    try:
        tz = ZoneInfo(getattr(brand, "timezone", None) or "UTC")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        tz = timezone.utc
    return now.replace(tzinfo=now.tzinfo or timezone.utc).astimezone(tz).date()


def _acquire_lane_lock(client, key: str, value: str) -> bool:
    import time

    deadline = time.monotonic() + LANE_LOCK_WAIT_SECONDS
    while True:
        if client.set(key, value, nx=True, px=LANE_LOCK_TTL_MS):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(LANE_LOCK_RETRY_SECONDS)


def _release_lane_lock(client, key: str, value: str) -> None:
    try:
        if client.get(key) == value:
            client.delete(key)
    except Exception:
        logger.warning("Group Share: lane reservation lock release failed", exc_info=True)


def reserve_lane_slot(db, fanpage_id: int, mode: str, now: datetime):
    """Reserve one of today's ig/news lane posts before AI work.

    Returns a token (the reservation key) to pass to release_lane_slot, None when the daily
    cap is reached, or False when no reservation is needed (not a lane, or Redis is down and
    the marker count still has room). Under a short Redis lock it counts today's group-job
    markers plus live reservations; each reservation expires on its own.
    """
    import uuid

    from app.models.target_fanpages import TargetFanpage
    from .feeder import _local_day_start_utc
    from .lanes import lane_brand_id

    if mode not in LANE_CAPPED_MODES:
        raise ValueError(f"No lane cap for mode {mode!r}")
    brand_id = lane_brand_id(db, fanpage_id)
    if brand_id is None:
        return False
    _, settings = get_brand_share(db, brand_id)
    cap = settings["sources"].get(mode, {}).get("groups_only_per_day", 0)
    if cap <= 0:
        return None
    brand = db.get(TargetFanpage, brand_id)
    start = _local_day_start_utc(brand, now)
    day = f"{brand_id}:{mode}:{_brand_local_date(brand, now).isoformat()}"

    def used_today() -> int:
        return db.query(FbShareGroupJob.id).filter(
            FbShareGroupJob.fanpage_id == brand_id, FbShareGroupJob.mode == mode,
            FbShareGroupJob.created_at >= start,
        ).count()

    try:
        client = redis_client()
        lock_key, lock_value = f"{LANE_LOCK_PREFIX}{day}", uuid.uuid4().hex
        if not _acquire_lane_lock(client, lock_key, lock_value):
            logger.warning("Group Share: lane reservation lock %s busy; skipping this %s item", lock_key, mode)
            return None
        try:
            live = sum(1 for _ in client.scan_iter(match=f"{LANE_RESERVATION_PREFIX}{day}:*"))
            if used_today() + live >= cap:
                return None
            token = f"{LANE_RESERVATION_PREFIX}{day}:{uuid.uuid4().hex}"
            client.set(token, 1, ex=LANE_RESERVATION_TTL_SECONDS)
            return token
        finally:
            _release_lane_lock(client, lock_key, lock_value)
    except Exception:
        logger.warning("Group Share: Redis lane reservation failed; using marker count", exc_info=True)
        return None if used_today() >= cap else False


def release_lane_slot(token) -> None:
    """Drop a reservation: on every path without a job, and right after the job is committed
    (its group-job marker counts from then on)."""
    if not (isinstance(token, str) and token.startswith(LANE_RESERVATION_PREFIX)):
        return
    try:
        redis_client().delete(token)
    except Exception:
        logger.warning("Group Share: Redis lane reservation release failed", exc_info=True)


def lane_daily_cap_reached(db, fanpage_id: int, mode: str, now: datetime) -> bool:
    """True when ``fanpage_id`` is a group lane that already made its daily ig/news quota.

    Counts today's group-job markers of the brand + mode in the brand's local day.
    Always False for a normal (non-lane) fanpage, so Page behaviour is unchanged.
    """
    from app.models.target_fanpages import TargetFanpage
    from .feeder import _local_day_start_utc
    from .lanes import lane_brand_id

    if mode not in LANE_CAPPED_MODES:
        raise ValueError(f"No lane cap for mode {mode!r}")
    brand_id = lane_brand_id(db, fanpage_id)
    if brand_id is None:
        return False
    _, settings = get_brand_share(db, brand_id)
    cap = settings["sources"].get(mode, {}).get("groups_only_per_day", 0)
    if cap <= 0:
        return True
    start = _local_day_start_utc(db.get(TargetFanpage, brand_id), now)
    used = db.query(FbShareGroupJob.id).filter(
        FbShareGroupJob.fanpage_id == brand_id, FbShareGroupJob.mode == mode,
        FbShareGroupJob.created_at >= start,
    ).count()
    return used >= cap


def hand_over(db, job: PublishJob, now) -> FbShareItem | None:
    from app.services.social_delivery_planner import build_delivery_payload
    from .feeder import MEDIA_KINDS, load_image_bytes
    from .lanes import lane_brand_id
    from app.services.radar_clustering import compute_phash

    row = db.query(FbShareGroupJob).filter_by(publish_job_id=job.id).with_for_update().first()
    if row is None:
        return None
    # A lane job's item belongs to the brand: the ledger, planner and settings are per brand.
    brand_id = lane_brand_id(db, job.fanpage_id) or job.fanpage_id
    item = db.query(FbShareItem).filter_by(
        fanpage_id=brand_id, source_kind="publish_job", source_ref=str(job.id),
    ).first()
    if item is not None and row.handed_at is not None:
        return item
    if job.status not in (PublishJobStatus.pending_publish, PublishJobStatus.pending_review) or job.last_error:
        return None
    now = _utc_naive(now)
    if item is None:
        _, settings = get_brand_share(db, brand_id)
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
            fanpage_id=brand_id, niche=niches[0] if len(niches) == 1 else "",
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


# Task states whose media is still needed to send or post. Anything else has already left the app (sent to the
# PIC, posted, or never retried), so its media only stays for previews.
MEDIA_NEEDED_TASK_STATES = ("planned", "publishing")
MEDIA_PREVIEW_DAYS = 7


def media_cleanup_allowed(db, publish_job_id: int, now: datetime) -> bool:
    """Keep group media while a share still needs it, then seven days for previews (owner 2026-11-10)."""
    row = db.query(FbShareGroupJob).filter_by(publish_job_id=publish_job_id).first()
    if row is None:
        return True
    item = db.query(FbShareItem).filter_by(
        fanpage_id=row.fanpage_id, source_kind="publish_job", source_ref=str(publish_job_id),
    ).first()
    # "new" items are not routed yet; routed items never change status again, so their tasks decide.
    if item is None or item.status not in ("routed", "done", "expired", "skipped"):
        return False
    tasks = db.query(FbShareTask).filter_by(item_id=item.id).all()
    if any(task.state in MEDIA_NEEDED_TASK_STATES for task in tasks):
        return False
    finished = [task.updated_at for task in tasks if task.updated_at]
    if not finished and item.expires_at:
        finished = [item.expires_at]
    return bool(finished) and (
        max(_utc_naive(value) for value in finished) + timedelta(days=MEDIA_PREVIEW_DAYS) <= _utc_naive(now)
    )
