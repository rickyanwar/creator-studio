"""Mode 6: Facebook photo content — clone another Facebook page's photos
(competitor/inspiration pages, not the fanpage's own) as news/discussion
content.

Same two-stage shape as Mode 5 Pinterest (see app.tasks.pinterest's module
docstring): candidates pulled from a curated FacebookPhotoSource's `/photos`
grid become staged FacebookPhotoIdea rows (already classified news/discussion
at topup time — see services.facebook_photo_source.build_idea_from_candidate),
consumed FIFO, paced by facebook_photo_daily_count, into a real PublishJob
that renders the SAME Facebook photo onto the resolved news/discussion
template (see design_renderer.render_facebook_photo).

v1 has NO like-count/growth-verification gate — "new to us" (fbid not
already downloaded) is the only filter. Deferred to a later phase, see
memory feature-facebook-trending-source.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

_WINDOW_START_HOUR = 8
_WINDOW_END_HOUR = 22

# Same reasoning as pinterest.py's _MIN_QUEUE_SIZE/_TOPUP_BATCH — topup a
# small batch per tick rather than the full deficit at once, so paid
# webfetch/vision spend spreads across ticks instead of bursting.
_MIN_QUEUE_SIZE = 5
_TOPUP_BATCH = 6

# Same bounded catch-up loop as pinterest.py's _MAX_CATCHUP_PER_TICK — fixes
# the burstiness half of a daily-quota gap (a tick whose topup nets 0
# survivors shouldn't permanently lose that tick's quota slot), does not
# fix a genuinely-too-low total survival rate (see pinterest.py's own
# comment on this for the full reasoning, unchanged here).
_MAX_CATCHUP_PER_TICK = 3


def _wib_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    day_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
    start_wib = day_wib.replace(hour=0, minute=0, second=0, microsecond=0)
    end_wib = start_wib + timedelta(days=1)
    return (
        start_wib.astimezone(timezone.utc).replace(tzinfo=None),
        end_wib.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _target_by_now(quota: int, now_wib_hour: float) -> int:
    if now_wib_hour < _WINDOW_START_HOUR:
        return 0
    if now_wib_hour >= _WINDOW_END_HOUR:
        return quota
    frac = (now_wib_hour - _WINDOW_START_HOUR) / (_WINDOW_END_HOUR - _WINDOW_START_HOUR)
    import math
    return min(quota, math.ceil(quota * frac + 0.0001) if frac > 0 else 1)


def _topup_queue(db, fanpage) -> int:
    """Fetch a small batch of new candidates from one curated source (LRU)
    and turn each that survives classification into a pending idea. Returns
    how many were added."""
    from sqlalchemy import func
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.models.facebook_photo_sources import FacebookPhotoSource
    from app.services.facebook_photo_source import fetch_photo_candidates, build_idea_from_candidate

    pending_count = (
        db.query(func.count(FacebookPhotoIdea.id))
        .filter(FacebookPhotoIdea.fanpage_id == fanpage.id, FacebookPhotoIdea.status == "pending")
        .scalar()
    ) or 0
    if pending_count >= _MIN_QUEUE_SIZE:
        return 0

    source = (
        db.query(FacebookPhotoSource)
        .filter_by(fanpage_id=fanpage.id, is_active=True)
        .order_by(FacebookPhotoSource.last_used_at.asc().nullsfirst(), FacebookPhotoSource.id.asc())
        .first()
    )
    if not source:
        return 0

    try:
        candidates = fetch_photo_candidates(source.page_url, limit=_TOPUP_BATCH)
    except Exception as exc:
        logger.error("Facebook photo: candidate fetch failed for source %d (%s): %s", source.id, source.page_url, exc)
        return 0

    source.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    source.times_used = (source.times_used or 0) + 1
    db.commit()

    created = 0
    for candidate in candidates:
        try:
            if build_idea_from_candidate(db, fanpage, candidate):
                created += 1
        except Exception as exc:
            logger.error("Facebook photo: idea build failed for fanpage %d: %s", fanpage.id, exc)
    return created


def _consume_one(db, fanpage) -> bool:
    """Pop the oldest pending idea into a PublishJob. Returns True if one
    was created."""
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType, AIProvider
    from app.models.target_fanpages import PublishMode
    from app.services.ai_caption import build_caption_prompt, generate_caption, GroqRateLimitError
    from app.services.design_images import resolve_template

    idea = (
        db.query(FacebookPhotoIdea)
        .filter(FacebookPhotoIdea.fanpage_id == fanpage.id, FacebookPhotoIdea.status == "pending")
        .order_by(FacebookPhotoIdea.created_at.asc())
        .first()
    )
    if not idea:
        return False

    template = resolve_template(db, idea.category, fanpage=fanpage)

    caption_context = idea.design_title
    try:
        caption, provider = generate_caption(
            build_caption_prompt(fanpage, fanpage.name, caption_context, source=None),
        )
    except GroqRateLimitError:
        # Leave the idea pending — a later tick retries it, same recovery
        # shape as ig_recreate's own GroqRateLimitError handling.
        return False

    job = PublishJob(
        fanpage_id=fanpage.id,
        post_id=None,
        content_type=ContentType.facebook_recreate,
        source_gallery_image_id=idea.gallery_image_id,
        design_title=idea.design_title,
        design_subtitle=idea.design_subtitle,
        design_caption=idea.design_caption,
        ai_generated_caption=caption,
        ai_provider_used=AIProvider(provider),
        design_template_id=template.id if template else None,
        status=PublishJobStatus.pending_design,
    )
    db.add(job)

    idea.status = "used"
    idea.used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    logger.info(
        "Facebook photo: fanpage %d created job %d from idea %d (%s) title=%r",
        fanpage.id, job.id, idea.id, idea.category, idea.design_title,
    )

    if fanpage.facebook_photo_publish_mode == PublishMode.auto:
        from app.tasks.design_renderer import render_facebook_photo
        render_facebook_photo.apply_async(args=[job.id], countdown=random.randint(5, 90))

    return True


@celery_app.task(name="app.tasks.facebook_photo.generate_facebook_photo_content")
def generate_facebook_photo_content():
    """Beat tick: top up each Mode-6 fanpage's idea queue, then consume
    toward today's paced quota."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        from app.models.target_fanpages import TargetFanpage
        from app.models.publish_jobs import PublishJob, ContentType

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        now_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
        now_hour = now_wib.hour + now_wib.minute / 60.0
        if now_hour < _WINDOW_START_HOUR or now_hour >= _WINDOW_END_HOUR:
            return

        day_start_utc, day_end_utc = _wib_day_bounds_utc(now_utc)

        fanpages = (
            db.query(TargetFanpage)
            .filter(
                TargetFanpage.facebook_photo_enabled == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
                TargetFanpage.facebook_photo_daily_count > 0,
            )
            .all()
        )

        topped_up = 0
        created = 0
        for fp in fanpages:
            topped_up += _topup_queue(db, fp)

            quota = fp.facebook_photo_daily_count or 0
            count_today = (
                db.query(func.count(PublishJob.id))
                .filter(
                    PublishJob.fanpage_id == fp.id,
                    PublishJob.content_type == ContentType.facebook_recreate,
                    PublishJob.is_deleted == False,
                    PublishJob.created_at >= day_start_utc,
                    PublishJob.created_at < day_end_utc,
                )
                .scalar()
            ) or 0

            target_now = _target_by_now(quota, now_hour)
            catchup_left = _MAX_CATCHUP_PER_TICK
            while count_today < quota and count_today < target_now and catchup_left > 0:
                if not _consume_one(db, fp):
                    break
                count_today += 1
                catchup_left -= 1
                created += 1

        if topped_up or created:
            logger.info(
                "Facebook photo sweep: +%d idea(s), %d job(s) across %d fanpage(s)",
                topped_up, created, len(fanpages),
            )
    finally:
        db.close()
