"""Social delivery planner module.

Builds immutable delivery payloads from publish jobs, computes canonical SHA-256
hashes, and ensures delivery ledger rows per active destination with local-day
slot allocation, method quotas, and media capability checks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import logging
from typing import Any, Sequence
import zoneinfo

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.event_days import news_fresh_hours
from app.services.publishing_schedule import (
    DEFAULT_ACTIVE_END_HOUR,
    DEFAULT_ACTIVE_START_HOUR,
)
from app.services.social_delivery_policy import (
    choose_method_for_slot,
    compute_next_slot,
)

logger = logging.getLogger(__name__)

OVERNIGHT_NEWS_LEAD_HOURS = 4   # news this close to the sleep window may wait for the morning
OVERNIGHT_NEWS_WAKE_HOURS = 3   # ...but only into the first hours after wake-up


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


def _resolve_aware_now(now_utc: datetime | None) -> datetime:
    if now_utc is None:
        return datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        return now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(timezone.utc)


def _resolve_sleep_window(db: Session, job: PublishJob) -> tuple[int | None, int | None]:
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
    return sleep_start, sleep_end


def _overnight_news_ok(
    slot_local: datetime,
    created_local: datetime,
    sleep_start: int,
    sleep_end: int,
) -> bool:
    sleep_len = (sleep_end - sleep_start) % 24
    if sleep_len == 0:
        return False
    wake = datetime.combine(slot_local.date(), time(sleep_end), tzinfo=slot_local.tzinfo)
    night_start = wake - timedelta(hours=sleep_len)
    return (
        night_start - timedelta(hours=OVERNIGHT_NEWS_LEAD_HOURS) <= created_local < wake
        and wake <= slot_local < wake + timedelta(hours=OVERNIGHT_NEWS_WAKE_HOURS)
    )


def _news_stale_reason(
    slot_utc: datetime,
    created_utc: datetime,
    stale_hours: int,
    tz_name: str | None,
    sleep_start: int | None = None,
    sleep_end: int | None = None,
) -> str | None:
    """Why a news slot is too late, or None when it is fine.

    Owner rule 2026-10-21 / 2026-10-30: news posts on the same local day it arrived
    (destination timezone) OR within overnight exception (OVERNIGHT_NEWS_LEAD_HOURS
    before sleep, OVERNIGHT_NEWS_WAKE_HOURS after wake), always within stale_hours.
    """
    try:
        tz = zoneinfo.ZoneInfo(tz_name) if tz_name else zoneinfo.ZoneInfo("Asia/Jakarta")
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Jakarta")

    slot_aware = slot_utc if slot_utc.tzinfo is not None else slot_utc.replace(tzinfo=timezone.utc)
    created_aware = created_utc if created_utc.tzinfo is not None else created_utc.replace(tzinfo=timezone.utc)

    if (slot_aware - created_aware) > timedelta(hours=stale_hours):
        return f"stale: no News slot within {stale_hours} h"

    slot_local = slot_aware.astimezone(tz)
    created_local = created_aware.astimezone(tz)

    if slot_local.date() == created_local.date():
        return None

    start = sleep_start if sleep_start is not None else DEFAULT_ACTIVE_END_HOUR
    end = sleep_end if sleep_end is not None else DEFAULT_ACTIVE_START_HOUR

    if _overnight_news_ok(slot_local, created_local, start, end):
        return None

    return "stale: no News slot on the day the news arrived"


def _plan_slot(
    db: Session,
    destination: SocialDestination,
    job: PublishJob,
    aware_now: datetime,
    sleep_start: int | None,
    sleep_end: int | None,
) -> tuple[datetime, bool, int, list[datetime]]:
    """Next slot for job on destination, whether that slot makes the news stale, and the stale limit in hours."""
    from app.services.content_groups import group_of
    from app.services.mode_quota import pick_slot
    from app.services.publishing_schedule import get_destination_reservations_utc

    existing_slots_utc = get_destination_reservations_utc(db, destination)

    dest_fanpage = getattr(destination, "content_project", None)
    if dest_fanpage is None and destination.content_project_id:
        dest_fanpage = db.query(TargetFanpage).filter_by(id=destination.content_project_id).first()
    dest_hour_weights = getattr(dest_fanpage, "publish_hour_weights", None) if dest_fanpage else None

    next_slot_utc = pick_slot(
        db=db,
        destination=destination,
        job=job,
        fanpage=dest_fanpage,
        now_utc=aware_now,
        all_reservations=existing_slots_utc,
        hour_weights=dest_hour_weights,
        sleep_start=sleep_start,
        sleep_end=sleep_end,
    )

    job_created_at = getattr(job, "created_at", None)
    if job_created_at is None:
        job_created_at_utc = aware_now
    elif job_created_at.tzinfo is None:
        job_created_at_utc = job_created_at.replace(tzinfo=timezone.utc)
    else:
        job_created_at_utc = job_created_at.astimezone(timezone.utc)
    if job_created_at_utc > aware_now:
        job_created_at_utc = aware_now

    try:
        dest_tz = zoneinfo.ZoneInfo(destination.timezone)
    except Exception:
        dest_tz = zoneinfo.ZoneInfo("Asia/Jakarta")
    local_day = aware_now.astimezone(dest_tz).date()
    news_stale_hours = news_fresh_hours(db, dest_fanpage, getattr(destination, "news_stale_hours", None), local_day)

    is_stale_news = (
        group_of(job.content_type) == "news"
        and _news_stale_reason(
            next_slot_utc,
            job_created_at_utc,
            news_stale_hours,
            getattr(destination, "timezone", None),
            sleep_start=sleep_start,
            sleep_end=sleep_end,
        ) is not None
    )

    return next_slot_utc, is_stale_news, news_stale_hours, existing_slots_utc


SUPERSEDE_REASON = "stale: superseded by fresher news (job {job_id})"


def _supersedable_news_delivery(
    db: Session,
    destination: SocialDestination,
    job: PublishJob,
    aware_now: datetime,
    stale_hours: int,
    *,
    lock: bool,
    sleep_start: int | None = None,
    sleep_end: int | None = None,
) -> PublishDelivery | None:
    """Oldest untouched news delivery on this destination whose slot would still be fresh for `job` (owner 2026-10-26: newest news first). None when there is none."""
    aware_now_utc = aware_now if aware_now.tzinfo is not None else aware_now.replace(tzinfo=timezone.utc)
    aware_now_naive = aware_now_utc.astimezone(timezone.utc).replace(tzinfo=None)

    job_created_at = getattr(job, "created_at", None)
    if job_created_at is None:
        job_created_naive = aware_now_naive
        job_created_aware = aware_now_utc
    else:
        if job_created_at.tzinfo is not None:
            job_created_naive = job_created_at.astimezone(timezone.utc).replace(tzinfo=None)
            job_created_aware = job_created_at.astimezone(timezone.utc)
        else:
            job_created_naive = job_created_at
            job_created_aware = job_created_at.replace(tzinfo=timezone.utc)

    if job_created_aware > aware_now_utc:
        job_created_aware = aware_now_utc

    filters = [
        PublishDelivery.destination_id == destination.id,
        PublishDelivery.state == "scheduled",
        PublishDelivery.repliz_schedule_id.is_(None),
        PublishDelivery.facebook_post_id.is_(None),
        PublishDelivery.uncertain_since.is_(None),
        PublishDelivery.claim_token.is_(None),
        PublishDelivery.attempt_count == 0,
        PublishDelivery.scheduled_for > aware_now_naive,
        PublishJob.content_type == ContentType.news_content,
        PublishJob.created_at < job_created_naive,
        PublishJob.is_breaking.is_(False),
    ]
    if getattr(job, "id", None) is not None:
        filters.append(PublishJob.id != job.id)

    query = (
        db.query(PublishDelivery)
        .join(PublishJob, PublishDelivery.publish_job_id == PublishJob.id)
        .filter(*filters)
        .order_by(PublishJob.created_at.asc())
        .limit(50)
    )

    if lock:
        query = query.with_for_update(of=PublishDelivery)

    rows = query.all()
    for deliv in rows:
        if deliv.scheduled_for is None:
            continue
        pj = getattr(deliv, "publish_job", None)
        if pj and pj.created_at is not None:
            pj_created = pj.created_at if pj.created_at.tzinfo is None else pj.created_at.astimezone(timezone.utc).replace(tzinfo=None)
            if pj_created >= job_created_naive:
                continue
        slot_aware = (
            deliv.scheduled_for
            if deliv.scheduled_for.tzinfo is not None
            else deliv.scheduled_for.replace(tzinfo=timezone.utc)
        )
        if (
            _news_stale_reason(
                slot_aware,
                job_created_aware,
                stale_hours,
                getattr(destination, "timezone", None),
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            )
            is None
        ):
            return deliv

    return None


def news_slot_available(
    db: Session,
    job: PublishJob,
    now_utc: datetime | None = None,
) -> bool:
    """True when at least one active destination of the job's fan page can post this news job within its news_stale_hours.

    Read-only (no row locks, no writes). True when the fan page has no active destinations or on any error (the old behaviour then applies).
    """
    try:
        fanpage_id = getattr(job, "fanpage_id", None)
        if fanpage_id is None:
            return True

        destinations = (
            db.query(SocialDestination)
            .filter(
                SocialDestination.content_project_id == fanpage_id,
                SocialDestination.is_active.is_(True),
            )
            .order_by(SocialDestination.id)
            .all()
        )
        if not destinations:
            return True

        sleep_start, sleep_end = _resolve_sleep_window(db, job)
        aware_now = _resolve_aware_now(now_utc)

        for destination in destinations:
            _, is_stale_news, news_stale_hours, _ = _plan_slot(
                db=db,
                destination=destination,
                job=job,
                aware_now=aware_now,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            )
            if not is_stale_news:
                return True
            if _supersedable_news_delivery(
                db,
                destination,
                job,
                aware_now,
                news_stale_hours,
                lock=False,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            ) is not None:
                return True

        return False
    except Exception as exc:
        job_id = getattr(job, "id", None)
        logger.warning(
            "news_slot_available failed for job id=%s: %s",
            job_id,
            exc,
        )
        return True


VIDEO_STOCK_RELEASE_HOURS = 36


def video_slot_soon(
    db: Session,
    job: PublishJob,
    now_utc: datetime | None = None,
    horizon_hours: float = VIDEO_STOCK_RELEASE_HOURS,
) -> bool:
    """True when at least one active destination of the job's fan page would get a slot for this job within horizon_hours.

    Read-only (no row locks, no writes). True when the fan page has no active destinations or on any error (old behaviour).
    """
    try:
        fanpage_id = getattr(job, "fanpage_id", None)
        if fanpage_id is None:
            return True

        destinations = (
            db.query(SocialDestination)
            .filter(
                SocialDestination.content_project_id == fanpage_id,
                SocialDestination.is_active.is_(True),
            )
            .order_by(SocialDestination.id)
            .all()
        )
        if not destinations:
            return True

        sleep_start, sleep_end = _resolve_sleep_window(db, job)
        aware_now = _resolve_aware_now(now_utc)
        deadline = aware_now + timedelta(hours=horizon_hours)

        for destination in destinations:
            next_slot_utc, _, _, _ = _plan_slot(
                db=db,
                destination=destination,
                job=job,
                aware_now=aware_now,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            )
            if next_slot_utc is not None:
                slot_aware = (
                    next_slot_utc
                    if next_slot_utc.tzinfo is not None
                    else next_slot_utc.replace(tzinfo=timezone.utc)
                )
                if slot_aware <= deadline:
                    return True

        return False
    except Exception as exc:
        job_id = getattr(job, "id", None)
        logger.warning(
            "video_slot_soon failed for job id=%s: %s",
            job_id,
            exc,
        )
        return True


def next_news_slot_utc(
    db: Session,
    job: PublishJob,
    now_utc: datetime | None = None,
) -> datetime | None:
    """Earliest next slot (aware UTC) over the job's fan page's active destinations. Read-only; None when there are no destinations or on error."""
    try:
        fanpage_id = getattr(job, "fanpage_id", None)
        if fanpage_id is None:
            return None

        destinations = (
            db.query(SocialDestination)
            .filter(
                SocialDestination.content_project_id == fanpage_id,
                SocialDestination.is_active.is_(True),
            )
            .order_by(SocialDestination.id)
            .all()
        )
        if not destinations:
            return None

        sleep_start, sleep_end = _resolve_sleep_window(db, job)
        aware_now = _resolve_aware_now(now_utc)

        slots: list[datetime] = []
        for destination in destinations:
            next_slot_utc, is_stale_news, news_stale_hours, _ = _plan_slot(
                db=db,
                destination=destination,
                job=job,
                aware_now=aware_now,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            )
            dest_slot = next_slot_utc
            if is_stale_news:
                old = _supersedable_news_delivery(
                    db,
                    destination,
                    job,
                    aware_now,
                    news_stale_hours,
                    lock=False,
                    sleep_start=sleep_start,
                    sleep_end=sleep_end,
                )
                if old is not None and old.scheduled_for is not None:
                    dest_slot = (
                        old.scheduled_for
                        if old.scheduled_for.tzinfo is not None
                        else old.scheduled_for.replace(tzinfo=timezone.utc)
                    )
            if dest_slot is not None:
                slots.append(dest_slot)

        return min(slots) if slots else None
    except Exception as exc:
        job_id = getattr(job, "id", None)
        logger.warning(
            "next_news_slot_utc failed for job id=%s: %s",
            job_id,
            exc,
        )
        return None


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

    sleep_start, sleep_end = _resolve_sleep_window(db, job)
    aware_now = _resolve_aware_now(now_utc)

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

        (
            next_slot_utc,
            is_stale_news,
            news_stale_hours,
            existing_slots_utc,
        ) = _plan_slot(
            db=db,
            destination=destination,
            job=job,
            aware_now=aware_now,
            sleep_start=sleep_start,
            sleep_end=sleep_end,
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

        platform = destination.kind
        platform_accepted = {"youtube": {"video"}, "x": {"image", "album", "video"}}.get(platform)
        if is_stale_news:
            old = _supersedable_news_delivery(
                db,
                destination,
                job,
                aware_now,
                news_stale_hours,
                lock=True,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            )
            if old is not None:
                freed_slot = old.scheduled_for
                old.state = "skipped"
                old.last_error = SUPERSEDE_REASON.format(job_id=job.id)
                old.scheduled_for = None

                old_deliveries = (
                    db.query(PublishDelivery)
                    .filter(PublishDelivery.publish_job_id == old.publish_job_id)
                    .all()
                )
                if old_deliveries and all(
                    d.state == "skipped" and (d.last_error or "").startswith("stale:")
                    for d in old_deliveries
                ):
                    old_job = (
                        db.query(PublishJob)
                        .filter(PublishJob.id == old.publish_job_id)
                        .first()
                    )
                    if old_job is not None:
                        old_job.status = PublishJobStatus.skipped
                        old_job.last_error = old.last_error

                logger.info(
                    "news job %s takes slot of older news job %s on destination %s",
                    job.id,
                    old.publish_job_id,
                    destination.id,
                )
                is_stale_news = False
                naive_scheduled_for = freed_slot

        if is_stale_news:
            state = "skipped"
            assigned_device_id = None
            naive_scheduled_for = None
            job_created_at = getattr(job, "created_at", None)
            if job_created_at is None:
                job_created_at_utc = aware_now
            elif job_created_at.tzinfo is None:
                job_created_at_utc = job_created_at.replace(tzinfo=timezone.utc)
            else:
                job_created_at_utc = job_created_at.astimezone(timezone.utc)
            if job_created_at_utc > aware_now:
                job_created_at_utc = aware_now
            last_error = _news_stale_reason(
                next_slot_utc,
                job_created_at_utc,
                news_stale_hours,
                destination.timezone,
                sleep_start=sleep_start,
                sleep_end=sleep_end,
            ) or f"stale: no News slot within {news_stale_hours} h"
        elif platform_accepted and payload.media_kind not in platform_accepted:
            state = "skipped"
            assigned_device_id = None
            last_error = f"Media not accepted by {destination.kind}"
        elif not is_supported:
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
