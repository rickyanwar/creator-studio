"""Fair dispatcher service for visual queue news jobs."""
from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import or_

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.engine_router import render_queue_for

logger = logging.getLogger(__name__)

_WIB = ZoneInfo("Asia/Jakarta")

LAST_JOB_KEY = "visual_fair:news:last_job"
JIT_LEAD_MINUTES = 30
CLAIM_KEY = "visual_fair:news:claim:{fanpage_id}"


def news_job_waiting(db, r) -> bool:
    """Return True if last dispatched news job is still waiting in queue (pending_design + lock held)."""
    try:
        last = r.get(LAST_JOB_KEY)
        if last is None:
            return False
        try:
            job_id = int(last)
        except (ValueError, TypeError):
            return False
        job = db.query(PublishJob).filter(PublishJob.id == job_id).first()
        if not job or job.status != PublishJobStatus.pending_design:
            return False
        return bool(r.exists(f"render_design:queued:{job_id}"))
    except Exception as exc:
        logger.warning("Failed checking if last news job is waiting: %s", exc)
        return False


def wib_date(now_utc: datetime) -> str:
    """Return date in Asia/Jakarta as YYYY-MM-DD."""
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    return now_utc.astimezone(_WIB).strftime("%Y-%m-%d")


def fair_counter_key(fanpage_id: int, now_utc: datetime) -> str:
    """Return Redis counter key for served news jobs."""
    return f"visual_fair:news:{wib_date(now_utc)}:{fanpage_id}"


def served_today(r, fanpage_ids: list[int] | set[int], now_utc: datetime) -> dict[int, int]:
    """Return dict of served counts today per fanpage using one MGET. 0 if missing/error."""
    ids = list(fanpage_ids)
    if not ids:
        return {}
    keys = [fair_counter_key(fid, now_utc) for fid in ids]
    try:
        vals = r.mget(keys)
        res = {}
        for fid, val in zip(ids, vals):
            try:
                res[fid] = int(val) if val is not None else 0
            except (ValueError, TypeError):
                res[fid] = 0
        return res
    except Exception as exc:
        logger.warning("Failed to fetch fair counters: %s", exc)
        return {fid: 0 for fid in ids}


def mark_served(r, fanpage_id: int, now_utc: datetime) -> None:
    """Increment served counter for fanpage and set 48h TTL. Swallow and log errors."""
    try:
        key = fair_counter_key(fanpage_id, now_utc)
        r.incr(key)
        r.expire(key, 172800)
    except Exception as exc:
        logger.warning("Failed to mark fanpage %s served: %s", fanpage_id, exc)


def _has_news_slot(db, job: PublishJob, now_utc: datetime) -> bool:
    """Check if job has an available delivery slot within 24h. Fail open on error."""
    try:
        from app.services.social_delivery_planner import news_slot_available

        return bool(news_slot_available(db, job, now_utc))
    except Exception as exc:
        logger.warning("Failed checking news slot for job %s: %s", getattr(job, "id", None), exc)
        return True


def _next_slot(db, job: PublishJob, now_utc: datetime) -> datetime | None:
    """Return earliest next news slot (aware UTC) over active destinations, or None on error."""
    try:
        from app.services.social_delivery_planner import next_news_slot_utc

        return next_news_slot_utc(db, job, now_utc)
    except Exception as exc:
        logger.warning("Failed checking next news slot for job %s: %s", getattr(job, "id", None), exc)
        return None


def page_claimed(r, fanpage_id: int) -> bool:
    """Return True if fan page is claimed for news design until next slot."""
    try:
        return bool(r.exists(CLAIM_KEY.format(fanpage_id=fanpage_id)))
    except Exception as exc:
        logger.warning("Failed checking page claim for fanpage %s: %s", fanpage_id, exc)
        return False


def claim_page(r, fanpage_id: int, slot_utc: datetime, now_utc: datetime) -> None:
    """Claim fan page until its next news slot so no other news renders for that slot. Errors are logged."""
    try:
        key = CLAIM_KEY.format(fanpage_id=fanpage_id)
        slot_aware = slot_utc if slot_utc.tzinfo else slot_utc.replace(tzinfo=timezone.utc)
        now_aware = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
        ttl = max(900, int((slot_aware - now_aware).total_seconds()) + 600)
        r.set(key, slot_utc.isoformat(), ex=ttl)
    except Exception as exc:
        logger.warning("Failed claiming fanpage %s: %s", fanpage_id, exc)


def release_page(r, fanpage_id: int) -> None:
    """Drop the JIT claim so the page's job can be picked again (used when a render is retried)."""
    try:
        r.delete(CLAIM_KEY.format(fanpage_id=fanpage_id))
    except Exception as exc:
        logger.warning("Failed releasing fanpage %s: %s", fanpage_id, exc)


def visual_news_fanpage_ids(db) -> set[int]:
    """Return all TargetFanpage IDs in auto mode routing news to 'visual'."""
    fps = db.query(TargetFanpage).filter(TargetFanpage.mode2_publish_mode == PublishMode.auto).all()
    return {fp.id for fp in fps if render_queue_for(db, fp, "news") == "visual"}


def pick_next_news_job(db, r, now_utc: datetime) -> PublishJob | None:
    """Pick next candidate news job to render on visual queue using fair scheduling."""
    from app.tasks.design_renderer import (
        NEWS_STALE_HOURS,
        _NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS,
    )

    visual_ids = visual_news_fanpage_ids(db)
    if not visual_ids:
        return None

    now_naive = now_utc.astimezone(timezone.utc).replace(tzinfo=None) if now_utc.tzinfo else now_utc
    stale_cutoff = now_naive - timedelta(hours=NEWS_STALE_HOURS)
    retry_cutoff = now_naive - timedelta(hours=_NEEDS_MANUAL_IMAGE_RETRY_COOLDOWN_HOURS)

    not_deferred = or_(
        PublishJob.design_not_before.is_(None),
        PublishJob.design_not_before <= now_naive,
    )
    not_stuck_on_missing_image = or_(
        PublishJob.last_error.is_(None),
        ~PublishJob.last_error.ilike("needs manual image%"),
        PublishJob.updated_at < retry_cutoff,
    )

    candidates = (
        db.query(PublishJob)
        .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
        .filter(
            PublishJob.status == PublishJobStatus.pending_design,
            PublishJob.content_type == ContentType.news_content,
            TargetFanpage.mode2_publish_mode == PublishMode.auto,
            PublishJob.created_at >= stale_cutoff,
            not_stuck_on_missing_image,
            not_deferred,
            PublishJob.fanpage_id.in_(visual_ids),
        )
        .all()
    )
    if not candidates:
        return None

    breaking_jobs = [j for j in candidates if j.is_breaking]
    breaking_jobs.sort(key=lambda j: j.created_at or datetime.min, reverse=True)

    non_breaking = [j for j in candidates if not j.is_breaking]
    sorted_fps: list[int] = []
    jobs_by_fp: dict[int, list[PublishJob]] = collections.defaultdict(list)
    if non_breaking:
        for j in non_breaking:
            jobs_by_fp[j.fanpage_id].append(j)

        for fp_id in jobs_by_fp:
            jobs_by_fp[fp_id].sort(key=lambda j: j.created_at or datetime.min, reverse=True)

        fp_ids = list(jobs_by_fp.keys())
        served = served_today(r, fp_ids, now_utc)

        # Ties broken by fan page whose newest job is newest (descending)
        fps_by_newest = sorted(
            fp_ids,
            key=lambda fid: jobs_by_fp[fid][0].created_at or datetime.min,
            reverse=True,
        )
        # Lowest served count first (ascending; Python stable sort preserves newest-first tie-breaker)
        sorted_fps = sorted(
            fps_by_newest,
            key=lambda fid: served.get(fid, 0),
        )

    candidates_checked = 0

    for job in breaking_jobs:
        if candidates_checked >= 20:
            return None
        candidates_checked += 1
        try:
            if r.exists(f"render_design:queued:{job.id}"):
                continue
        except Exception:
            pass
        if not _has_news_slot(db, job, now_utc):
            job.status = PublishJobStatus.skipped
            job.last_error = "stale: no News slot on the news day or within 24 h, AI poster not rendered"
            db.commit()
            continue
        return job

    for fid in sorted_fps:
        if candidates_checked >= 20:
            return None
        if page_claimed(r, fid):
            continue

        for job in jobs_by_fp[fid]:
            if candidates_checked >= 20:
                return None
            try:
                if r.exists(f"render_design:queued:{job.id}"):
                    continue
            except Exception:
                pass
            candidates_checked += 1

            slot = _next_slot(db, job, now_utc)
            if slot is None:
                if not _has_news_slot(db, job, now_utc):
                    job.status = PublishJobStatus.skipped
                    job.last_error = "stale: no News slot on the news day or within 24 h, AI poster not rendered"
                    db.commit()
                    continue
                return job

            slot_aware = slot if slot.tzinfo else slot.replace(tzinfo=timezone.utc)
            now_aware = now_utc if now_utc.tzinfo else now_utc.replace(tzinfo=timezone.utc)
            if (slot_aware - now_aware) > timedelta(minutes=JIT_LEAD_MINUTES):
                break

            if not _has_news_slot(db, job, now_utc):
                job.status = PublishJobStatus.skipped
                job.last_error = "stale: no News slot on the news day or within 24 h, AI poster not rendered"
                db.commit()
                continue

            job._jit_slot = slot
            return job

    return None
