"""Social media brands, pacing, estimated slots, and Repliz accounts API router."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import logging
from typing import Optional
import zoneinfo

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DB
from app.api.settings import _get_or_create_settings
from app.models.publish_jobs import PublishJob
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.schemas.social_brands import (
    AIImagesTodayOut,
    AttentionOut,
    BrandDetailOut,
    BrandGptcliIn,
    BrandGptcliOut,
    BrandSummaryOut,
    ChannelDeviceOut,
    ChannelSummaryOut,
    ContentModeOut,
    EstimatedSlotOut,
    EstimatedTimesOut,
    RecentDeliveryOut,
    ReplizAccountOut,
    ReplizAccountsOut,
    TodayCountsOut,
)
from app.services.publishing_schedule import (
    compute_audience_slot,
    get_destination_reservations_utc,
)
from app.services.repliz_client import get_repliz_client_from_db
from app.services.social_delivery_policy import choose_method_for_slot

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/social-media", tags=["social-media"])

_WIB_TZ = zoneinfo.ZoneInfo("Asia/Jakarta")


def _utcnow() -> datetime:
    """Return current aware UTC instant. Monkeypatched in tests."""
    return datetime.now(timezone.utc)


def _tz_name(dest: SocialDestination, project: Optional[TargetFanpage]) -> str:
    return (dest.timezone or (project.timezone if project else None) or "Europe/London").strip()


def _local_day_window_utc(tz_name: str, now_utc: datetime) -> tuple[datetime, datetime, date]:
    try:
        dest_tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        raise HTTPException(status_code=422, detail=f"Invalid destination timezone: {tz_name}")
    now_loc = now_utc.astimezone(dest_tz)
    local_date = now_loc.date()
    start_loc = datetime(local_date.year, local_date.month, local_date.day, 0, 0, 0, tzinfo=dest_tz)
    end_loc = start_loc + timedelta(days=1)
    start_naive_utc = start_loc.astimezone(timezone.utc).replace(tzinfo=None)
    end_naive_utc = end_loc.astimezone(timezone.utc).replace(tzinfo=None)
    return start_naive_utc, end_naive_utc, local_date


def _channel_today(
    db: DB,
    dest: SocialDestination,
    project: Optional[TargetFanpage],
    now_utc: datetime,
) -> tuple[TodayCountsOut, int, int]:
    tz = _tz_name(dest, project)
    start_naive_utc, end_naive_utc, _ = _local_day_window_utc(tz, now_utc)

    published = (
        db.query(func.count(PublishDelivery.id))
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state == "published",
            PublishDelivery.published_at >= start_naive_utc,
            PublishDelivery.published_at < end_naive_utc,
        )
        .scalar()
        or 0
    )

    scheduled = (
        db.query(func.count(PublishDelivery.id))
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state.in_([
                "waiting_for_slot",
                "scheduled",
                "repliz_scheduled",
                "waiting_for_device",
                "publishing",
            ]),
            PublishDelivery.scheduled_for >= start_naive_utc,
            PublishDelivery.scheduled_for < end_naive_utc,
        )
        .scalar()
        or 0
    )

    failed = (
        db.query(func.count(PublishDelivery.id))
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state == "failed",
            func.coalesce(PublishDelivery.updated_at, PublishDelivery.created_at) >= start_naive_utc,
            func.coalesce(PublishDelivery.updated_at, PublishDelivery.created_at) < end_naive_utc,
        )
        .scalar()
        or 0
    )

    needs_human_cnt = (
        db.query(func.count(PublishDelivery.id))
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state == "needs_human",
        )
        .scalar()
        or 0
    )

    uncertain_cnt = (
        db.query(func.count(PublishDelivery.id))
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state == "uncertain",
        )
        .scalar()
        or 0
    )

    counts = TodayCountsOut(
        published=published,
        scheduled=scheduled,
        failed=failed,
        needs_attention=needs_human_cnt + uncertain_cnt,
    )
    return counts, needs_human_cnt, uncertain_cnt


def _next_post_at(db: DB, dest: SocialDestination, now_utc: datetime) -> Optional[datetime]:
    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None)
    val = (
        db.query(func.min(PublishDelivery.scheduled_for))
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state.in_([
                "waiting_for_slot",
                "scheduled",
                "repliz_scheduled",
                "waiting_for_device",
            ]),
            PublishDelivery.scheduled_for >= now_naive,
        )
        .scalar()
    )
    return val.replace(tzinfo=timezone.utc) if val is not None else None


def _channel_attention(
    dest: SocialDestination,
    counts: TodayCountsOut,
    devices: list[ChannelDeviceOut],
    needs_human_cnt: int,
    uncertain_cnt: int,
) -> list[AttentionOut]:
    items: list[AttentionOut] = []
    if needs_human_cnt > 0:
        items.append(
            AttentionOut(
                level="error",
                message=f"{needs_human_cnt} post(s) need a human check",
                channel_id=dest.id,
            )
        )
    if uncertain_cnt > 0:
        items.append(
            AttentionOut(
                level="warn",
                message=f"{uncertain_cnt} post(s) uncertain — never resubmitted",
                channel_id=dest.id,
            )
        )
    if counts.failed > 0:
        items.append(
            AttentionOut(
                level="warn",
                message=f"{counts.failed} post(s) failed today",
                channel_id=dest.id,
            )
        )
    if dest.mode in ("phone", "mixed") and not devices:
        items.append(
            AttentionOut(
                level="error",
                message="No phone assigned",
                channel_id=dest.id,
            )
        )
    if dest.mode in ("phone", "mixed") and dest.identity_verified_at is None:
        items.append(
            AttentionOut(
                level="warn",
                message="Phone identity not verified",
                channel_id=dest.id,
            )
        )
    if dest.mode in ("repliz", "mixed") and not dest.repliz_account_id:
        items.append(
            AttentionOut(
                level="error",
                message="No Repliz account connected",
                channel_id=dest.id,
            )
        )
    if not dest.is_active:
        items.append(
            AttentionOut(
                level="info",
                message="Paused",
                channel_id=dest.id,
            )
        )
    return items


CONTENT_MODES_SPEC = [
    ("ig_repost", "IG repost", "mode1_ig_repost_enabled", None, "image/video"),
    ("news", "News cards", "mode2_news_content_enabled", None, "image"),
    ("ig_recreate", "IG recreate", "ig_recreate_enabled", None, "image"),
    ("discussion", "Discussion cards", "discussion_enabled", "discussion_daily_count", "image"),
    ("pinterest", "Pinterest cards", "pinterest_enabled", "pinterest_daily_count", "image"),
    ("facebook_photo", "FB photo recreate", "facebook_photo_enabled", "facebook_photo_daily_count", "image"),
    ("yt_clip", "YouTube clips", "yt_clip_enabled", "yt_clip_daily_count", "video"),
    ("ai_video", "AI video", "ai_video_enabled", "ai_video_daily_count", "video"),
    ("radar", "Viral Radar", "radar_enabled", "radar_daily_max", "image"),
]


def _get_brand_content_modes(fp: TargetFanpage) -> list[ContentModeOut]:
    res = []
    for key, label, enabled_field, count_field, media in CONTENT_MODES_SPEC:
        enabled = bool(getattr(fp, enabled_field, False))
        count = getattr(fp, count_field, None) if count_field else None
        res.append(
            ContentModeOut(
                key=key,
                label=label,
                enabled=enabled,
                daily_count=count,
                media=media,
            )
        )
    return res


def _build_channel_summary(
    db: DB,
    dest: SocialDestination,
    project: Optional[TargetFanpage],
    now_utc: datetime,
) -> ChannelSummaryOut:
    assignments = (
        db.query(DestinationDeviceAssignment)
        .options(joinedload(DestinationDeviceAssignment.device))
        .filter(
            DestinationDeviceAssignment.destination_id == dest.id,
            DestinationDeviceAssignment.is_active == True,
        )
        .order_by(DestinationDeviceAssignment.priority.asc())
        .all()
    )
    devices = [
        ChannelDeviceOut(
            device_id=a.device_id,
            name=a.device.name if a.device else "",
            status=a.device.status if a.device else "maintenance",
            priority=a.priority,
            last_seen_at=a.device.last_seen_at.replace(tzinfo=timezone.utc)
            if (a.device and a.device.last_seen_at)
            else None,
        )
        for a in assignments
    ]

    today_counts, needs_human_cnt, uncertain_cnt = _channel_today(db, dest, project, now_utc)
    next_post = _next_post_at(db, dest, now_utc)
    attention = _channel_attention(dest, today_counts, devices, needs_human_cnt, uncertain_cnt)

    supported_media = dest.supported_media
    if not isinstance(supported_media, list):
        supported_media = list(supported_media or [])

    return ChannelSummaryOut(
        id=dest.id,
        content_project_id=dest.content_project_id,
        kind=dest.kind,
        name=dest.name,
        facebook_actor_id=dest.facebook_actor_id,
        parent_profile_id=dest.parent_profile_id,
        repliz_account_id=dest.repliz_account_id,
        mode=dest.mode,
        timezone=dest.timezone,
        daily_target=dest.daily_target,
        phone_daily_target=dest.phone_daily_target,
        repliz_daily_target=dest.repliz_daily_target,
        min_gap_minutes=dest.min_gap_minutes,
        supported_media=supported_media,
        is_active=dest.is_active,
        identity_verified_at=dest.identity_verified_at.replace(tzinfo=timezone.utc)
        if dest.identity_verified_at
        else None,
        devices=devices,
        today=today_counts,
        next_post_at=next_post,
        attention=attention,
    )


def _build_brand_summary(
    db: DB,
    brand: TargetFanpage,
    settings,
    now_utc: datetime,
) -> BrandSummaryOut:
    gptcli_ids = set(settings.gptcli_fanpage_ids or [])
    gptcli_enabled_for_brand = brand.id in gptcli_ids
    gptcli_global_enabled = bool(settings.gptcli_enabled)

    destinations = (
        db.query(SocialDestination)
        .filter(SocialDestination.content_project_id == brand.id)
        .order_by(SocialDestination.id.asc())
        .all()
    )

    channels = [_build_channel_summary(db, dest, brand, now_utc) for dest in destinations]

    today = TodayCountsOut(
        published=sum(c.today.published for c in channels),
        scheduled=sum(c.today.scheduled for c in channels),
        failed=sum(c.today.failed for c in channels),
        needs_attention=sum(c.today.needs_attention for c in channels),
    )

    valid_next_posts = [c.next_post_at for c in channels if c.next_post_at is not None]
    next_post_at = min(valid_next_posts) if valid_next_posts else None

    attention: list[AttentionOut] = []
    if not brand.is_active:
        attention.append(AttentionOut(level="info", message="Brand is paused", channel_id=None))
    for c in channels:
        attention.extend(c.attention)

    pub_mode = (
        brand.publish_mode.value
        if hasattr(brand.publish_mode, "value")
        else (str(brand.publish_mode) if brand.publish_mode is not None else None)
    )

    niche = (
        brand.mode2_gallery_niches[0]
        if (brand.mode2_gallery_niches and len(brand.mode2_gallery_niches) > 0)
        else None
    )

    return BrandSummaryOut(
        id=brand.id,
        name=brand.name,
        is_active=brand.is_active,
        timezone=brand.timezone,
        target_country=brand.target_country,
        publish_mode=pub_mode,
        publish_daily_limit=brand.publish_daily_limit,
        publish_sleep_start_hour=brand.publish_sleep_start_hour,
        publish_sleep_end_hour=brand.publish_sleep_end_hour,
        template_style=getattr(brand, "template_style", None),
        niche=niche,
        gptcli_enabled_for_brand=gptcli_enabled_for_brand,
        gptcli_global_enabled=gptcli_global_enabled,
        content=_get_brand_content_modes(brand),
        channels=channels,
        today=today,
        next_post_at=next_post_at,
        attention=attention,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.get("/brands", response_model=list[BrandSummaryOut])
def get_brands(db: DB, _: CurrentUser):
    now_utc = _utcnow()
    settings = _get_or_create_settings(db)
    brands = (
        db.query(TargetFanpage)
        .order_by(TargetFanpage.is_active.desc(), TargetFanpage.name.asc())
        .all()
    )
    return [_build_brand_summary(db, b, settings, now_utc) for b in brands]


@router.get("/brands/{brand_id}", response_model=BrandDetailOut)
def get_brand(brand_id: int, db: DB, _: CurrentUser):
    now_utc = _utcnow()
    brand = db.query(TargetFanpage).filter_by(id=brand_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    settings = _get_or_create_settings(db)
    summary = _build_brand_summary(db, brand, settings, now_utc)

    now_wib = now_utc.astimezone(_WIB_TZ)
    wib_midnight = datetime(now_wib.year, now_wib.month, now_wib.day, 0, 0, 0, tzinfo=_WIB_TZ)
    wib_midnight_naive_utc = wib_midnight.astimezone(timezone.utc).replace(tzinfo=None)
    day_start_wib = wib_midnight.isoformat()

    engine_counts = {"chatgpt": 0, "codex": 0, "codex_fallback": 0, "template": 0}
    jobs = (
        db.query(PublishJob.design_engine, func.count(PublishJob.id))
        .filter(
            PublishJob.fanpage_id == brand_id,
            PublishJob.updated_at >= wib_midnight_naive_utc,
            PublishJob.design_engine.in_(["chatgpt", "codex", "codex_fallback", "template"]),
        )
        .group_by(PublishJob.design_engine)
        .all()
    )
    for engine, cnt in jobs:
        if engine in engine_counts:
            engine_counts[engine] = cnt

    return BrandDetailOut(
        **summary.model_dump(),
        ai_images_today=AIImagesTodayOut(**engine_counts),
        day_start_wib=day_start_wib,
    )


@router.put("/brands/{brand_id}/gptcli", response_model=BrandGptcliOut)
def set_brand_gptcli(brand_id: int, payload: BrandGptcliIn, db: DB, _: CurrentUser):
    brand = db.query(TargetFanpage).filter_by(id=brand_id).first()
    if not brand:
        raise HTTPException(status_code=404, detail="Brand not found")

    row = _get_or_create_settings(db)
    current_ids = set(row.gptcli_fanpage_ids or [])
    if payload.enabled:
        new_ids = sorted(current_ids | {brand_id})
    else:
        new_ids = sorted(current_ids - {brand_id})

    row.gptcli_fanpage_ids = new_ids
    db.commit()
    db.refresh(row)

    return BrandGptcliOut(
        brand_id=brand_id,
        enabled_for_brand=brand_id in new_ids,
        gptcli_fanpage_ids=new_ids,
        gptcli_global_enabled=bool(row.gptcli_enabled),
    )


@router.get("/destinations/{destination_id}/estimated-times", response_model=EstimatedTimesOut)
def get_destination_estimated_times(destination_id: int, db: DB, _: CurrentUser):
    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")

    project = db.query(TargetFanpage).filter_by(id=dest.content_project_id).first()
    tz_name = _tz_name(dest, project)
    now_utc = _utcnow()
    start_naive_utc, end_naive_utc, local_date = _local_day_window_utc(tz_name, now_utc)
    dest_tz = zoneinfo.ZoneInfo(tz_name)

    daily_target = int(dest.daily_target) if dest.daily_target and dest.daily_target > 0 else 1
    min_gap = int(dest.min_gap_minutes) if dest.min_gap_minutes is not None and dest.min_gap_minutes >= 0 else 5
    sleep_start = project.publish_sleep_start_hour if project else None
    sleep_end = project.publish_sleep_end_hour if project else None

    # 1. Real slots
    deliveries = (
        db.query(PublishDelivery)
        .filter(
            PublishDelivery.destination_id == dest.id,
            PublishDelivery.state != "skipped",
        )
        .all()
    )

    real_slots: list[EstimatedSlotOut] = []
    for d in deliveries:
        instant = d.published_at if d.published_at is not None else d.scheduled_for
        if instant is not None and start_naive_utc <= instant < end_naive_utc:
            if d.state == "published":
                slot_status = "published"
            elif d.state == "failed":
                slot_status = "failed"
            elif d.state in ("publishing", "waiting_for_device", "uncertain", "needs_human"):
                slot_status = "in_progress"
            elif d.state in ("waiting_for_slot", "scheduled", "repliz_scheduled"):
                slot_status = "scheduled"
            else:
                slot_status = d.state

            method = d.actual_method or d.intended_method
            at = instant.replace(tzinfo=timezone.utc)
            real_slots.append(
                EstimatedSlotOut(
                    index=0,
                    at=at,
                    local_time=at.astimezone(dest_tz).strftime("%H:%M"),
                    wib_time=at.astimezone(_WIB_TZ).strftime("%H:%M"),
                    method=method,
                    status=slot_status,
                    delivery_id=d.id,
                )
            )

    # 2. Remaining slots
    non_failed_count = sum(1 for s in real_slots if s.status != "failed")
    remaining = daily_target - non_failed_count

    # 3. Simulated slots
    estimated_slots: list[EstimatedSlotOut] = []
    if remaining > 0:
        try:
            existing_reservations = get_destination_reservations_utc(db, dest)
        except Exception:
            raise HTTPException(status_code=503, detail="Unable to retrieve existing publishing reservations")

        simulated_reservations = list(existing_reservations)
        max_loops = remaining + 3

        for _ in range(max_loops):
            try:
                next_slot = compute_audience_slot(
                    now_utc=now_utc,
                    timezone_name=tz_name,
                    daily_target=daily_target,
                    min_gap_minutes=min_gap,
                    existing_slots_utc=simulated_reservations,
                    sleep_start_hour=sleep_start,
                    sleep_end_hour=sleep_end,
                    namespace=f"dest-{dest.id}",
                )
            except ValueError as e:
                raise HTTPException(status_code=422, detail=f"Invalid schedule parameters: {e}")
            except Exception:
                raise HTTPException(status_code=503, detail="Unable to calculate upcoming schedule slots")

            if next_slot is None:
                break

            slot_naive_utc = next_slot.astimezone(timezone.utc).replace(tzinfo=None)
            if not (start_naive_utc <= slot_naive_utc < end_naive_utc):
                break

            slot_local_date = next_slot.astimezone(dest_tz).date()
            if slot_local_date != local_date:
                break

            idx = sum(
                1
                for s in simulated_reservations
                if s.astimezone(dest_tz).date() == slot_local_date
            )

            try:
                method = choose_method_for_slot(
                    mode=dest.mode,
                    slot_index=idx,
                    phone_daily_target=dest.phone_daily_target,
                    repliz_daily_target=dest.repliz_daily_target,
                )
            except ValueError:
                method = None

            simulated_reservations.append(next_slot)
            estimated_slots.append(
                EstimatedSlotOut(
                    index=0,
                    at=next_slot,
                    local_time=next_slot.astimezone(dest_tz).strftime("%H:%M"),
                    wib_time=next_slot.astimezone(_WIB_TZ).strftime("%H:%M"),
                    method=method,
                    status="estimated",
                    delivery_id=None,
                )
            )

            if len(estimated_slots) >= remaining:
                break

    # 4. Sort and assign index
    all_slots = real_slots + estimated_slots
    all_slots.sort(key=lambda s: s.at)
    for i, slot in enumerate(all_slots):
        slot.index = i

    note = (
        "No more slots today (quiet hours or past the window)"
        if len(all_slots) < daily_target
        else None
    )

    return EstimatedTimesOut(
        destination_id=dest.id,
        timezone=tz_name,
        date=local_date.strftime("%Y-%m-%d"),
        daily_target=daily_target,
        mode=dest.mode,
        phone_daily_target=dest.phone_daily_target,
        repliz_daily_target=dest.repliz_daily_target,
        slots=all_slots,
        note=note,
    )


@router.get("/destinations/{destination_id}/recent", response_model=list[RecentDeliveryOut])
def get_destination_recent_deliveries(
    destination_id: int,
    db: DB,
    _: CurrentUser,
    limit: int = Query(20, ge=1, le=50),
):
    dest = db.query(SocialDestination).filter_by(id=destination_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="Destination not found")

    order_col = func.coalesce(
        PublishDelivery.published_at,
        PublishDelivery.scheduled_for,
        PublishDelivery.created_at,
    ).desc()

    deliveries = (
        db.query(PublishDelivery)
        .options(joinedload(PublishDelivery.publish_job))
        .filter(PublishDelivery.destination_id == destination_id)
        .order_by(order_col)
        .limit(limit)
        .all()
    )

    res: list[RecentDeliveryOut] = []
    for d in deliveries:
        job = d.publish_job
        content_type = (
            job.content_type.value
            if (job and hasattr(job.content_type, "value"))
            else (str(job.content_type) if (job and job.content_type) else None)
        )
        title = None
        if job and job.design_title:
            title = job.design_title
        elif d.title_snapshot:
            title = d.title_snapshot
        elif d.caption_snapshot:
            title = d.caption_snapshot[:80]

        method = d.actual_method or d.intended_method
        last_error = d.last_error[:300] if d.last_error else None

        res.append(
            RecentDeliveryOut(
                id=d.id,
                state=d.state,
                method=method,
                scheduled_for=d.scheduled_for.replace(tzinfo=timezone.utc) if d.scheduled_for else None,
                published_at=d.published_at.replace(tzinfo=timezone.utc) if d.published_at else None,
                facebook_permalink=d.facebook_permalink,
                last_error=last_error,
                publish_job_id=d.publish_job_id,
                content_type=content_type,
                title=title,
                design_engine=job.design_engine if job else None,
                design_engine_note=job.design_engine_note if job else None,
            )
        )

    return res


@router.get("/repliz-accounts", response_model=ReplizAccountsOut)
def get_repliz_accounts(db: DB, _: CurrentUser):
    try:
        client = get_repliz_client_from_db(db)
    except RuntimeError:
        return ReplizAccountsOut(configured=False, error=None, accounts=[])
    except Exception as exc:
        logger.warning("Could not initialize Repliz client: %s", type(exc).__name__)
        return ReplizAccountsOut(
            configured=True,
            error=f"Could not reach Repliz ({type(exc).__name__})",
            accounts=[],
        )

    try:
        with client:
            raw_docs = client.list_facebook_accounts()
    except Exception as exc:
        logger.warning("Could not reach Repliz: %s", type(exc).__name__)
        return ReplizAccountsOut(
            configured=True,
            error=f"Could not reach Repliz ({type(exc).__name__})",
            accounts=[],
        )

    destinations = (
        db.query(SocialDestination)
        .filter(SocialDestination.repliz_account_id.isnot(None))
        .all()
    )
    dest_map = {d.repliz_account_id: d for d in destinations}

    accounts: list[ReplizAccountOut] = []
    if isinstance(raw_docs, list):
        for doc in raw_docs:
            if not isinstance(doc, dict):
                continue
            acc_id = str(doc.get("_id") or doc.get("id") or "").strip()
            if not acc_id:
                continue

            used_dest = dest_map.get(acc_id)
            name = str(doc.get("name") or doc.get("username") or "")
            username = doc.get("username") if isinstance(doc.get("username"), str) else None
            picture = next((v for v in (doc.get("picture"), doc.get("avatar")) if isinstance(v, str) and v), None)

            accounts.append(
                ReplizAccountOut(
                    id=acc_id,
                    name=name,
                    username=username,
                    picture=picture,
                    type="facebook",
                    used_by_destination_id=used_dest.id if used_dest else None,
                    used_by_destination_name=used_dest.name if used_dest else None,
                )
            )

    return ReplizAccountsOut(configured=True, error=None, accounts=accounts)
