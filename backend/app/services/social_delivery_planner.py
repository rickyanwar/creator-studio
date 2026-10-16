"""Social delivery planner module.

Builds immutable delivery payloads from publish jobs, computes canonical SHA-256
hashes, and ensures delivery ledger rows per active destination with local-day
slot allocation, method quotas, and media capability checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any, Sequence
import zoneinfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PublishDelivery,
    SocialDestination,
)
from app.services.social_delivery_policy import (
    choose_method_for_slot,
    compute_next_slot,
)

logger = logging.getLogger(__name__)


def compute_caption_hash(caption: str | None) -> str:
    """Compute canonical SHA-256 hash for a UTF-8 caption."""
    return hashlib.sha256((caption or "").encode("utf-8")).hexdigest()


def compute_media_hash(media: Sequence[Any]) -> str:
    """Compute canonical SHA-256 hash for a media descriptor sequence."""
    canonical_json = json.dumps(list(media), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeliveryPayload:
    """Immutable snapshot payload for a content delivery."""

    caption: str
    media_kind: str
    media_refs: tuple[str, ...]
    title: str | None
    alt: str | None
    content_type: str

    @property
    def caption_sha256(self) -> str:
        return compute_caption_hash(self.caption)

    @property
    def media_sha256(self) -> str:
        return compute_media_hash(self.media_refs)

    def to_dict(self) -> dict[str, Any]:
        return {
            "caption": self.caption,
            "media_kind": self.media_kind,
            "media_refs": list(self.media_refs),
            "title": self.title,
            "alt": self.alt,
            "content_type": self.content_type,
            "caption_sha256": self.caption_sha256,
            "media_sha256": self.media_sha256,
        }

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


def build_delivery_payload(
    job: PublishJob,
    post: Post | None = None,
) -> DeliveryPayload:
    """Build an immutable DeliveryPayload from a PublishJob and optional Post.

    Handles IG reposts (single image or album), rendered design images, and
    videos. Rejects absent media.
    """
    ct = getattr(job, "content_type", ContentType.ig_repost)
    ct_val = ct.value if hasattr(ct, "value") else str(ct)

    title = getattr(job, "design_title", None)
    alt: str | None = None

    if ct_val == ContentType.ig_repost.value or ct_val == "ig_repost":
        if post is None:
            post = getattr(job, "post", None)

        caption = (
            getattr(job, "ai_generated_caption", None)
            or (getattr(post, "original_caption", None) if post else None)
            or ""
        )

        watermarked = getattr(job, "watermarked_image_urls", None)
        if watermarked:
            media_refs = [str(u) for u in watermarked if u]
        else:
            pub_urls = [
                str(u) for u in (getattr(post, "image_public_urls", None) or []) if u
            ]
            src_urls = [
                str(u) for u in (getattr(post, "image_source_urls", None) or []) if u
            ]
            local_paths = [
                str(p) for p in (getattr(post, "image_local_paths", None) or []) if p
            ]

            if pub_urls:
                if "localhost" in pub_urls[0] and src_urls:
                    media_refs = src_urls
                else:
                    media_refs = pub_urls
            elif src_urls:
                media_refs = src_urls
            elif local_paths:
                media_refs = local_paths
            else:
                media_refs = []

        if not media_refs:
            raise ValueError(
                f"Job {getattr(job, 'id', None)}: absent media for ig_repost"
            )

        media_type = getattr(post, "media_type", None)
        mt_val = (
            media_type.value if hasattr(media_type, "value") else str(media_type or "")
        )
        if (mt_val == "album" or len(media_refs) >= 2) and len(media_refs) >= 2:
            media_kind = "album"
        else:
            media_kind = "image"

    elif ct_val in (
        ContentType.news_content.value,
        ContentType.ig_recreate.value,
        ContentType.discussion.value,
        ContentType.pinterest_content.value,
        ContentType.facebook_recreate.value,
        "news_content",
        "ig_recreate",
        "discussion",
        "pinterest_content",
        "facebook_recreate",
    ):
        caption = getattr(job, "ai_generated_caption", None) or ""
        img_url = getattr(job, "design_image_url", None)
        img_path = getattr(job, "design_image_path", None)

        if img_url:
            media_refs = [str(img_url)]
        elif img_path:
            media_refs = [str(img_path)]
        else:
            raise ValueError(
                f"Job {getattr(job, 'id', None)}: absent media for {ct_val}"
            )

        media_kind = "image"
        alt = title

    elif ct_val in (
        ContentType.youtube_clip.value,
        ContentType.ai_video.value,
        "youtube_clip",
        "ai_video",
    ):
        caption = getattr(job, "ai_generated_caption", None) or ""
        vid_url = getattr(job, "video_url", None)
        vid_path = getattr(job, "video_path", None)

        if vid_url:
            media_refs = [str(vid_url)]
        elif vid_path:
            media_refs = [str(vid_path)]
        else:
            raise ValueError(
                f"Job {getattr(job, 'id', None)}: absent media for {ct_val}"
            )

        media_kind = "video"

    else:
        # Generic fallback
        caption = getattr(job, "ai_generated_caption", None) or ""
        vid_url = getattr(job, "video_url", None) or getattr(job, "video_path", None)
        img_url = getattr(job, "design_image_url", None) or getattr(
            job, "design_image_path", None
        )
        watermarked = getattr(job, "watermarked_image_urls", None)

        if vid_url:
            media_refs = [str(vid_url)]
            media_kind = "video"
        elif img_url:
            media_refs = [str(img_url)]
            media_kind = "image"
            alt = title
        elif watermarked:
            media_refs = [str(u) for u in watermarked if u]
            media_kind = "album" if len(media_refs) >= 2 else "image"
        else:
            raise ValueError(
                f"Job {getattr(job, 'id', None)}: absent media for {ct_val}"
            )

    return DeliveryPayload(
        caption=caption,
        media_kind=media_kind,
        media_refs=tuple(media_refs),
        title=title,
        alt=alt,
        content_type=ct_val,
    )


def ensure_deliveries(
    db: Session,
    job: PublishJob,
    now_utc: datetime | None = None,
) -> list[PublishDelivery]:
    """Ensure delivery rows exist for all active destinations of a publish job.

    Freezes immutable snapshot fields, allocates local-day slots with method quotas,
    respects supported media capabilities, and returns existing rows unchanged on retry.
    """
    post = getattr(job, "post", None)
    if post is None and getattr(job, "post_id", None):
        post = db.query(Post).filter_by(id=job.post_id).first()

    payload = build_delivery_payload(job, post=post)

    fanpage = getattr(job, "fanpage", None)
    if fanpage is None and getattr(job, "fanpage_id", None):
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()

    sleep_start = None
    sleep_end = None
    if (
        fanpage
        and fanpage.publish_sleep_start_hour is not None
        and fanpage.publish_sleep_end_hour is not None
    ):
        sleep_start = fanpage.publish_sleep_start_hour
        sleep_end = fanpage.publish_sleep_end_hour

    if now_utc is None:
        aware_now = datetime.now(timezone.utc)
    elif now_utc.tzinfo is None:
        aware_now = now_utc.replace(tzinfo=timezone.utc)
    else:
        aware_now = now_utc.astimezone(timezone.utc)

    # Row lock active destinations to ensure serialized slot allocation per destination
    destinations = (
        db.query(SocialDestination)
        .filter(
            SocialDestination.content_project_id == job.fanpage_id,
            SocialDestination.is_active.is_(True),
        )
        .order_by(SocialDestination.id)
        .with_for_update()
        .all()
    )

    deliveries: list[PublishDelivery] = []

    for destination in destinations:
        existing = (
            db.query(PublishDelivery)
            .filter(
                PublishDelivery.publish_job_id == job.id,
                PublishDelivery.destination_id == destination.id,
            )
            .first()
        )
        if existing is not None:
            # Ponytail: return existing row unchanged; upgrade to versioned replanning if H1 permits.
            deliveries.append(existing)
            continue

        supported = destination.supported_media or []
        if isinstance(supported, str):
            try:
                supported = json.loads(supported)
            except Exception:
                supported = [supported]
        is_supported = payload.media_kind in supported

        from app.services.publishing_schedule import (
            compute_audience_slot,
            get_destination_reservations_utc,
        )

        existing_slots_utc = get_destination_reservations_utc(db, destination)

        next_slot_utc = compute_audience_slot(
            now_utc=aware_now,
            timezone_name=destination.timezone,
            daily_target=destination.daily_target,
            min_gap_minutes=destination.min_gap_minutes,
            existing_slots_utc=existing_slots_utc,
            sleep_start_hour=sleep_start,
            sleep_end_hour=sleep_end,
            namespace=f"dest-{destination.id}",
        )
        naive_scheduled_for = next_slot_utc.astimezone(timezone.utc).replace(
            tzinfo=None
        )

        target_tz = zoneinfo.ZoneInfo(destination.timezone)
        slot_local_date = next_slot_utc.astimezone(target_tz).date()
        existing_on_day = sum(
            1
            for s in existing_slots_utc
            if s.astimezone(target_tz).date() == slot_local_date
        )

        intended_method = choose_method_for_slot(
            mode=destination.mode,
            slot_index=existing_on_day,
            phone_daily_target=destination.phone_daily_target,
            repliz_daily_target=destination.repliz_daily_target,
        )

        if not is_supported:
            state = "waiting_for_slot"
            assigned_device_id = None
            last_error = f"Unsupported media kind '{payload.media_kind}' for destination"
        elif intended_method == "phone":
            assigned_device_id = None
            has_available = False
            assignments = (
                db.query(DestinationDeviceAssignment)
                .filter(
                    DestinationDeviceAssignment.destination_id == destination.id,
                    DestinationDeviceAssignment.is_active.is_(True),
                )
                .order_by(DestinationDeviceAssignment.priority)
                .all()
            )
            for a in assignments:
                if a.device and a.device.status == "available":
                    has_available = True
                    assigned_device_id = a.device.id
                    break

            if has_available:
                state = "scheduled"
                last_error = None
            else:
                state = "waiting_for_device"
                last_error = "Device not available"
        else:  # repliz
            state = "scheduled"
            assigned_device_id = None
            last_error = None

        delivery = PublishDelivery(
            publish_job_id=job.id,
            destination_id=destination.id,
            device_id=assigned_device_id,
            state=state,
            intended_method=intended_method,
            actual_method=None,
            scheduled_for=naive_scheduled_for,
            caption_snapshot=payload.caption,
            media_snapshot=list(payload.media_refs),
            media_kind=payload.media_kind,
            title_snapshot=payload.title,
            alt_snapshot=payload.alt,
            content_type_snapshot=payload.content_type,
            caption_sha256=payload.caption_sha256,
            media_sha256=payload.media_sha256,
            content_version=1,
            last_error=last_error,
        )

        try:
            with db.begin_nested():
                db.add(delivery)
                db.flush()
        except IntegrityError:
            existing = (
                db.query(PublishDelivery)
                .filter(
                    PublishDelivery.publish_job_id == job.id,
                    PublishDelivery.destination_id == destination.id,
                )
                .first()
            )
            if existing is not None:
                deliveries.append(existing)
                continue
            raise

        event = DeliveryEvent(
            delivery_id=delivery.id,
            event_type="planned",
            details={
                "state": state,
                "intended_method": intended_method,
                "media_kind": payload.media_kind,
                "content_type": payload.content_type,
                "caption_sha256": payload.caption_sha256,
                "media_sha256": payload.media_sha256,
                "scheduled_for": naive_scheduled_for.isoformat()
                if naive_scheduled_for
                else None,
                "reason": last_error,
            },
        )
        db.add(event)
        db.flush()

        logger.info(
            "Planned delivery id=%s job_id=%s dest_id=%s state=%s method=%s caption_sha=%s",
            delivery.id,
            job.id,
            destination.id,
            state,
            intended_method,
            payload.caption_sha256,
        )
        deliveries.append(delivery)

    return deliveries
