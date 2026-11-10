"""Copy recently published Facebook delivery snapshots into the sharing ledger."""

from collections import Counter
from datetime import datetime, timedelta, timezone
import logging
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from app.config import get_settings
from app.models.fb_group_share import FbShareGroupJob, FbShareItem
from app.models.publish_jobs import PublishJob
from app.models.radar import RadarStoryDecision
from app.models.social_publishing import PublishDelivery, SocialDestination
from .brand_settings import get_brand_share
from app.services.radar_clustering import compute_phash

logger = logging.getLogger(__name__)
MAX_IMAGE_BYTES = 15 * 1024 * 1024


MODE_KEYS = {
    "ig_repost": "ig", "ig_recreate": "ig", "news_content": "news",
    "discussion": "discussion", "pinterest_content": "pinterest",
    "facebook_recreate": "fb_recreate", "youtube_clip": "yt_clips",
    "ai_video": "ai_video",
}
MEDIA_KINDS = {"image": "photo", "album": "album", "video": "video"}


def _media_refs(snapshot):
    if isinstance(snapshot, (str, dict)):
        snapshot = [snapshot]
    refs = []
    for entry in snapshot if isinstance(snapshot, list) else []:
        if isinstance(entry, dict):
            entry = entry.get("url") or entry.get("path")
        if isinstance(entry, str) and entry.strip():
            refs.append(entry)
    return refs


def load_image_bytes(ref: str) -> bytes:
    if ref.startswith(("http://", "https://")):
        with httpx.stream("GET", ref, timeout=15.0, follow_redirects=True) as response:
            response.raise_for_status()
            data = bytearray()
            for chunk in response.iter_bytes():
                if len(data) + len(chunk) > MAX_IMAGE_BYTES:
                    raise ValueError("image_too_large")
                data.extend(chunk)
            return bytes(data)
    path = Path(ref)
    if not path.is_file():
        relative = ref.removeprefix("/").removeprefix("media/")
        root = Path(get_settings().storage_base_path).resolve()
        path = (root / relative).resolve()
        path.relative_to(root)
    with path.open("rb") as image:
        data = image.read(MAX_IMAGE_BYTES + 1)
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("image_too_large")
    return data


def _local_day_start_utc(fanpage, now: datetime) -> datetime:
    """Start of the brand's local day as naive UTC (brand timezone, fallback UTC)."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        zone = ZoneInfo(getattr(fanpage, "timezone", None) or "UTC")
    except (ZoneInfoNotFoundError, ValueError, TypeError):
        zone = ZoneInfo("UTC")
    start = now.astimezone(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc).replace(tzinfo=None)


def _job_modes(db, job_ids) -> dict[int, str]:
    """Group Share mode key per publish job id (radar-linked IG jobs count as viral_radar)."""
    job_ids = set(job_ids)
    if not job_ids:
        return {}
    radar_ids = {
        job_id for (job_id,) in db.query(RadarStoryDecision.publish_job_id).filter(
            RadarStoryDecision.publish_job_id.in_(job_ids)
        ).all()
    }
    modes = {}
    for job_id, content_type in db.query(PublishJob.id, PublishJob.content_type).filter(PublishJob.id.in_(job_ids)):
        key = getattr(content_type, "value", content_type)
        mode = "viral_radar" if job_id in radar_ids else MODE_KEYS.get(key)
        if mode:
            modes[job_id] = mode
    return modes


def _followed_today(db, fanpage, day_start: datetime) -> Counter:
    """Page-post items collected since the brand's local midnight, per mode (group jobs excluded)."""
    job_ids = {
        int(ref) for (ref,) in db.query(FbShareItem.source_ref).filter(
            FbShareItem.fanpage_id == fanpage.id, FbShareItem.source_kind == "publish_job",
            FbShareItem.created_at >= day_start,
        ) if ref.isdigit()
    }
    if job_ids:
        job_ids -= {
            job_id for (job_id,) in db.query(FbShareGroupJob.publish_job_id).filter(
                FbShareGroupJob.publish_job_id.in_(job_ids)
            )
        }
    return Counter(_job_modes(db, job_ids).values())


def collect_group_only_items(db, fanpage, now: datetime) -> int:
    from .group_jobs import hand_over

    jobs = db.query(PublishJob).join(
        FbShareGroupJob, FbShareGroupJob.publish_job_id == PublishJob.id,
    ).filter(FbShareGroupJob.fanpage_id == fanpage.id, FbShareGroupJob.handed_at.is_(None)).order_by(
        FbShareGroupJob.created_at, FbShareGroupJob.id,
    ).limit(10).all()
    job_ids = [job.id for job in jobs]
    count = 0
    for job_id in job_ids:
        try:
            item = hand_over(db, db.get(PublishJob, job_id), now)
            db.commit()
            count += item is not None
        except Exception:
            db.rollback()
            logger.warning("Group Share: hand-over failed for publish job %s", job_id, exc_info=True)
    return count


def collect_published_items(db, fanpage, now: datetime, *, loader=None) -> int:
    _, settings = get_brand_share(db, fanpage.id)
    niches = settings["niches"]
    if not niches:
        return 0
    day_start = _local_day_start_utc(fanpage, now)
    naive_now = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo else now
    # Only Page posts published TODAY in the brand's local day: yesterday evening's posts are stale.
    window_start = max(naive_now - timedelta(hours=48), day_start)
    rows = (
        db.query(PublishDelivery, PublishJob)
        .join(SocialDestination, PublishDelivery.destination_id == SocialDestination.id)
        .join(PublishJob, PublishDelivery.publish_job_id == PublishJob.id)
        .filter(
            PublishDelivery.state == "published",
            PublishDelivery.published_at >= window_start,
            SocialDestination.content_project_id == fanpage.id,
            SocialDestination.kind.in_(("facebook_profile", "facebook_page")),
        )
        # Newest first; for one job delivered to several destinations the first delivery wins.
        .order_by(PublishDelivery.published_at.desc(), PublishDelivery.id)
        .all()
    )
    modes = _job_modes(db, {job.id for _, job in rows})
    existing = {
        ref for (ref,) in db.query(FbShareItem.source_ref).filter_by(
            fanpage_id=fanpage.id, source_kind="publish_job"
        )
    }
    taken = _followed_today(db, fanpage, day_start) if rows else Counter()
    count = 0
    for delivery, job in rows:
        mode = modes.get(job.id)
        ref = str(job.id)
        if ref in existing or not mode or taken[mode] >= settings["sources"][mode].get("follow_per_day", 0):
            continue
        media = _media_refs(delivery.media_snapshot)
        media_kind = MEDIA_KINDS.get(delivery.media_kind)
        if not media or not media_kind:
            continue
        # Video covers/first frames are out of scope; dedupe videos by source_ref only.
        phash = None
        if media_kind != "video":
            try:
                phash = compute_phash((loader or load_image_bytes)(media[0]))
            except Exception:
                logger.warning("Group Share: skipping publish job %s: first media phash unavailable", job.id)
                continue
        caption = delivery.caption_snapshot or ""
        match = re.search(r"(?:\s|^)(#\w+(?:\s+#\w+)*)\s*$", caption)
        hashtags = " ".join(match.group(1).split()) if match else ""
        caption = caption[:match.start()].rstrip() if match else caption
        db.add(FbShareItem(
            fanpage_id=fanpage.id, niche=niches[0] if len(niches) == 1 else "",
            source_kind="publish_job", source_ref=ref, media_kind=media_kind,
            media=media, phash=phash, caption=caption, hashtags=hashtags, status="new",
            created_at=now, expires_at=now + timedelta(hours=12 if mode == "news" else 72),
        ))
        existing.add(ref)
        taken[mode] += 1
        count += 1
    db.commit()
    return count
