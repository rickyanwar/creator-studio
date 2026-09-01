"""Mode 5: Pinterest content — a photo is the seed instead of text.

Unlike Mode 2/4 (discover → job in one step), Mode 5 has a staging queue in
between (see app.models.pinterest_content_ideas.PinterestContentIdea):
candidates pulled from Pinterest (AI-keyword search and/or curated
profile/board references — app.services.pinterest_source) become editable
idea rows (title + description + a bound GalleryImage), and only later —
FIFO, paced by pinterest_daily_count — get converted into an actual
PublishJob. The beat task `generate_pinterest_content` ticks every 30 min,
WIB 08:00-22:00, and per fanpage does two independent things:
  1. top up the idea queue if it's running low (_topup_queue)
  2. consume the oldest pending idea(s) into jobs, if under today's paced
     quota (_consume_one, called in a bounded catch-up loop — see
     _MAX_CATCHUP_PER_TICK — so a tick that follows one or more empty/
     under-target ticks can close the gap instead of losing that quota
     slot forever)
render_design/render_discussion's photo-sourcing steps have no equivalent
here — the photo was already chosen (and vision-verified) when its idea was
created, so render_pinterest (design_renderer.py) just loads it directly.
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

# Keep the queue topped up to this many pending ideas per fanpage; only
# fetch a small batch per tick (not the full deficit at once) so the paid
# webfetch/vision spend spreads across ticks instead of bursting.
#
# _TOPUP_BATCH raised 3->6 2026-08-31: real production logs showed the
# candidate->idea survival rate is low and volatile (watermark/description
# -match/identify rejections) — one observed tick fetched 3 candidates and
# kept 0 as ideas. With _consume_one popping at most 1 idea/tick, a queue
# that nets less than ~1 surviving idea/tick on average drains and stays
# empty, which is why a fanpage with pinterest_daily_count=25 was only
# actually producing 6-8 jobs/day despite the 08:00-22:00 WIB window having
# ~28 ticks to work with. Fetching more raw candidates per topup gives the
# same survival RATE more chances to clear the 1/tick consumption bar.
_MIN_QUEUE_SIZE = 5
_TOPUP_BATCH = 6

# Even with the topup fix above, a single tick whose topup nets 0 survivors
# (queue momentarily empty right when the sweep tries to consume) used to
# cost that tick's quota slot PERMANENTLY — consume was capped at exactly 1
# idea/tick with no way to make it up later, so a BURSTY-but-otherwise-
# sufficient survival rate (a dry patch followed by a run of good ticks)
# still landed under `pinterest_daily_count` even though enough ideas
# existed across the day to hit it. 2026-09-01: real fanpage
# (pinterest_daily_count=25) still only hit 16/day after the topup fix.
# This catch-up loop fixes the BURSTINESS half of that gap — it does NOT
# fix a genuinely too-low TOTAL daily survival rate (if topup nets fewer
# than `pinterest_daily_count` surviving ideas across the whole window on
# average, no amount of consume-side catch-up manufactures more supply;
# `_TOPUP_BATCH`/`_MIN_QUEUE_SIZE` are the levers for that, separately).
# Capped at `_MAX_CATCHUP_PER_TICK` so a queue that suddenly has a big
# buffer doesn't dump the whole day's remaining quota into one post-burst.
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
    """Fetch a small batch of new candidates and turn each that survives
    vision verification into a pending idea. Returns how many were added."""
    from sqlalchemy import func
    from app.models.pinterest_content_ideas import PinterestContentIdea
    from app.services.pinterest_source import collect_new_candidates, build_idea_from_candidate

    pending_count = (
        db.query(func.count(PinterestContentIdea.id))
        .filter(PinterestContentIdea.fanpage_id == fanpage.id, PinterestContentIdea.status == "pending")
        .scalar()
    ) or 0
    if pending_count >= _MIN_QUEUE_SIZE:
        return 0

    mode = (fanpage.pinterest_source_mode or "both").lower()
    try:
        candidates = collect_new_candidates(db, fanpage, mode, limit=_TOPUP_BATCH)
    except Exception as exc:
        logger.error("Pinterest: candidate collection failed for fanpage %d: %s", fanpage.id, exc)
        return 0

    created = 0
    for candidate, source_type, _source_ref in candidates:
        try:
            if build_idea_from_candidate(db, fanpage, candidate, source_type):
                created += 1
        except Exception as exc:
            logger.error("Pinterest: idea build failed for fanpage %d: %s", fanpage.id, exc)
    return created


def _consume_one(db, fanpage) -> bool:
    """Pop the oldest pending idea into a PublishJob. Returns True if one
    was created."""
    from pathlib import Path

    from app.models.pinterest_content_ideas import PinterestContentIdea
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
    from app.models.target_fanpages import PublishMode
    from app.models.gallery import GalleryImage
    from app.services.design_images import resolve_template, _dominant_face_bbox

    idea = (
        db.query(PinterestContentIdea)
        .filter(PinterestContentIdea.fanpage_id == fanpage.id, PinterestContentIdea.status == "pending")
        .order_by(PinterestContentIdea.created_at.asc())
        .first()
    )
    if not idea:
        return False

    # No dedicated Mode 5 template — reuses the Quote/News pools, picked by
    # whether the idea's bound photo has a detected face (see
    # design_renderer.render_pinterest, which re-derives the same category
    # at render time; this early pin on job.design_template_id is just so
    # the Queue UI shows a sensible template before the job actually
    # renders, same as render_design/render_discussion already do).
    gi = db.query(GalleryImage).filter_by(id=idea.gallery_image_id).first()
    try:
        from app.services.pinterest_classifier import classify_pinterest_content
        category = classify_pinterest_content(idea.title, idea.description)
    except Exception as e:
        logger.error(f"Failed to classify pinterest idea {idea.id}: {e}")
        has_quote = any(q in (idea.title or "") for q in ['"', '“', '”'])
        category = "quote" if has_quote else "news"
    template = resolve_template(db, category, fanpage=fanpage)

    # The idea's description doubles as the actual Facebook post caption —
    # Mode 5 has no separate copywriting step (see module docstring) — with
    # hashtags appended fresh here (never baked into idea.description, which
    # stays clean for the design/queue display). Generated at consume time
    # rather than idea-creation time so a later pinterest_custom_prompt edit
    # still applies to ideas that were already queued.
    post_caption = idea.description
    if fanpage.pinterest_hashtag_count:
        try:
            from app.services.news_copywriter import generate_pinterest_hashtags
            hashtags = generate_pinterest_hashtags(fanpage, idea.title, idea.description)
            if hashtags:
                post_caption = f"{idea.description}\n\n{hashtags}"
        except Exception as exc:
            logger.warning("Pinterest: hashtag generation failed for idea %d: %s", idea.id, exc)

    job = PublishJob(
        fanpage_id=fanpage.id,
        post_id=None,
        content_type=ContentType.pinterest_content,
        source_gallery_image_id=idea.gallery_image_id,
        design_title=idea.title,
        design_caption=idea.description,
        ai_generated_caption=post_caption,
        design_template_id=template.id if template else None,
        status=PublishJobStatus.pending_design,
    )
    db.add(job)

    idea.status = "used"
    idea.used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()

    logger.info(
        "Pinterest: fanpage %d created job %d from idea %d (%s) title=%r",
        fanpage.id, job.id, idea.id, idea.source_type, idea.title,
    )

    if fanpage.pinterest_publish_mode == PublishMode.auto:
        from app.tasks.design_renderer import render_pinterest
        render_pinterest.apply_async(args=[job.id], countdown=random.randint(5, 90))

    return True


@celery_app.task(name="app.tasks.pinterest.generate_pinterest_content")
def generate_pinterest_content():
    """Beat tick: top up each Mode-5 fanpage's idea queue, then consume one
    idea toward today's paced quota."""
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
                TargetFanpage.pinterest_enabled == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
                TargetFanpage.pinterest_daily_count > 0,
            )
            .all()
        )

        topped_up = 0
        created = 0
        for fp in fanpages:
            topped_up += _topup_queue(db, fp)

            quota = fp.pinterest_daily_count or 0
            count_today = (
                db.query(func.count(PublishJob.id))
                .filter(
                    PublishJob.fanpage_id == fp.id,
                    PublishJob.content_type == ContentType.pinterest_content,
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
                "Pinterest sweep: +%d idea(s), %d job(s) across %d fanpage(s)",
                topped_up, created, len(fanpages),
            )
    finally:
        db.close()
