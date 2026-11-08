"""Copy recently published Facebook delivery snapshots into the sharing ledger."""

from datetime import datetime, timedelta
import logging
from pathlib import Path
import re

import httpx

from app.config import get_settings
from app.models.fb_group_share import FbShareItem
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


def collect_published_items(db, fanpage, now: datetime, *, loader=None) -> int:
    _, settings = get_brand_share(db, fanpage.id)
    niches = settings["niches"]
    if not niches:
        return 0
    rows = (
        db.query(PublishDelivery, PublishJob)
        .join(SocialDestination, PublishDelivery.destination_id == SocialDestination.id)
        .join(PublishJob, PublishDelivery.publish_job_id == PublishJob.id)
        .filter(
            PublishDelivery.state == "published",
            PublishDelivery.published_at >= now - timedelta(hours=48),
            SocialDestination.content_project_id == fanpage.id,
            SocialDestination.kind.in_(("facebook_profile", "facebook_page")),
        )
        .order_by(PublishDelivery.published_at, PublishDelivery.id)
        .all()
    )
    radar_job_ids = {
        job_id for (job_id,) in db.query(RadarStoryDecision.publish_job_id).filter(
            RadarStoryDecision.publish_job_id.in_({job.id for _, job in rows})
        ).all()
    } if rows else set()
    existing = {
        ref for (ref,) in db.query(FbShareItem.source_ref).filter_by(
            fanpage_id=fanpage.id, source_kind="publish_job"
        )
    }
    count = 0
    for delivery, job in rows:
        mode = "viral_radar" if job.id in radar_job_ids else MODE_KEYS.get(job.content_type)
        ref = str(job.id)
        if ref in existing or not mode or settings["sources"][mode]["source"] not in ("follow", "both"):
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
        count += 1
    db.commit()
    return count
