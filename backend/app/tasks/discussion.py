"""Mode 4: AI discussion / hot-take content — quota-driven per fanpage.

Unlike Mode 2 (one card per incoming article), Mode 4 is paced: the beat task
`generate_discussion_content` ticks every 30 min and, for each fanpage with
Mode 4 enabled, creates debate cards up to `discussion_daily_count` per WIB day,
spread evenly across an active window (08:00–22:00 WIB) so they don't all post
at once. Topics come from:
  - "news": the freshest scraped article from the fanpage's subscribed news
    sources that hasn't already been turned into a job for this fanpage
    (fact-grounded — the article is passed to the copywriter).
  - "evergreen": the least-recently-used active DiscussionTopic seed
    (opinion-only — see news_copywriter.generate_discussion_copy).
  - "both": try news first, fall back to evergreen.

Each card becomes a PublishJob(content_type=discussion, status=pending_design):
  design_title=question, design_subtitle=label, design_caption=subject name.
The renderer (design_renderer.render_discussion) sources the photo from the
subject and draws the card; the publisher reuses the news single-image path.
"""

import logging
import random
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))

# Active window (WIB) across which the daily quota is spread. No cards are
# generated outside it — a real page admin isn't posting debates at 4am.
_WINDOW_START_HOUR = 8
_WINDOW_END_HOUR = 22

# Only draw news topics from articles scraped within this many days — a debate
# card should ride current news, not last month's.
_NEWS_FRESH_DAYS = 3


def _wib_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    """(start, end) of the current WIB calendar day, as naive-UTC datetimes to
    match the DB's stored timestamps."""
    day_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
    start_wib = day_wib.replace(hour=0, minute=0, second=0, microsecond=0)
    end_wib = start_wib + timedelta(days=1)
    return (
        start_wib.astimezone(timezone.utc).replace(tzinfo=None),
        end_wib.astimezone(timezone.utc).replace(tzinfo=None),
    )


def _target_by_now(quota: int, now_wib_hour: float) -> int:
    """How many cards SHOULD exist by this point in the active window, so the
    quota trickles out across the day instead of all at once. Before the window
    opens → 0; at/after it closes → the full quota."""
    if now_wib_hour < _WINDOW_START_HOUR:
        return 0
    if now_wib_hour >= _WINDOW_END_HOUR:
        return quota
    frac = (now_wib_hour - _WINDOW_START_HOUR) / (_WINDOW_END_HOUR - _WINDOW_START_HOUR)
    # +1 so the first card can fire as soon as the window opens.
    import math
    return min(quota, math.ceil(quota * frac + 0.0001) if frac > 0 else 1)


def _pick_news_article(db, fanpage):
    """Freshest article from the fanpage's subscribed sources that has no
    existing publish job for this fanpage (dedupes across every mode via the
    uq_article_fanpage constraint). Returns a ScrapedArticle or None."""
    from sqlalchemy import func
    from app.models.scraped_articles import ScrapedArticle
    from app.models.fanpage_news_sources import FanpageNewsSource
    from app.models.publish_jobs import PublishJob

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=_NEWS_FRESH_DAYS)

    jobbed = (
        db.query(PublishJob.source_article_id)
        .filter(PublishJob.fanpage_id == fanpage.id, PublishJob.source_article_id.isnot(None))
    )

    rows = (
        db.query(ScrapedArticle)
        .join(FanpageNewsSource, FanpageNewsSource.news_source_id == ScrapedArticle.news_source_id)
        .filter(
            FanpageNewsSource.fanpage_id == fanpage.id,
            FanpageNewsSource.is_active == True,
            ScrapedArticle.scraped_at >= cutoff,
            ScrapedArticle.id.notin_(jobbed),
        )
        .order_by(ScrapedArticle.scraped_at.desc())
        .limit(12)
        .all()
    )
    return random.choice(rows) if rows else None


def _pick_evergreen_topic(db, fanpage):
    """Least-recently-used active evergreen seed for this fanpage, or None."""
    from app.models.discussion_topics import DiscussionTopic

    return (
        db.query(DiscussionTopic)
        .filter(DiscussionTopic.fanpage_id == fanpage.id, DiscussionTopic.is_active == True)
        .order_by(DiscussionTopic.last_used_at.asc().nullsfirst(), DiscussionTopic.id.asc())
        .first()
    )


def _create_one(db, fanpage) -> bool:
    """Generate a single discussion card for the fanpage. Returns True if a job
    was created. Picks the topic source per discussion_topic_mode; on 'both',
    news is preferred with an evergreen fallback."""
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
    from app.models.target_fanpages import PublishMode
    from app.services.news_copywriter import generate_discussion_copy
    from app.services.design_images import resolve_template

    mode = (fanpage.discussion_topic_mode or "both").lower()

    article = None
    topic = None
    if mode in ("news", "both"):
        article = _pick_news_article(db, fanpage)
    if article is None and mode in ("evergreen", "both"):
        topic = _pick_evergreen_topic(db, fanpage)

    if article is None and topic is None:
        logger.info("Discussion: fanpage %d has no available topic (mode=%s)", fanpage.id, mode)
        return False

    try:
        if article is not None:
            copy = generate_discussion_copy(fanpage, article=article)
        else:
            copy = generate_discussion_copy(
                fanpage, seed_text=topic.seed_text, subject_hint=topic.subject_hint
            )
    except Exception as exc:
        logger.error("Discussion: copy generation failed for fanpage %d: %s", fanpage.id, exc)
        return False

    # Prefer a discussion-tagged template; fall back to the fanpage's News
    # template if none is set/seeded (render_discussion applies the same
    # fallback, this just pins a sensible template_id on the job up front).
    template = resolve_template(db, "discussion", fanpage=fanpage) or resolve_template(db, "news", fanpage=fanpage)

    job = PublishJob(
        fanpage_id=fanpage.id,
        post_id=None,
        content_type=ContentType.discussion,
        source_article_id=article.id if article is not None else None,
        design_title=copy.question,
        design_subtitle=copy.label,          # badge text ("DISCUSSION"/"HOT TAKE")
        design_caption=copy.subject_name,    # subject for photo sourcing (not rendered)
        ai_generated_caption=copy.caption,
        ai_provider_used=copy.provider,
        design_template_id=template.id if template else None,
        status=PublishJobStatus.pending_design,
    )
    db.add(job)

    if topic is not None:
        topic.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        topic.times_used = (topic.times_used or 0) + 1

    db.commit()

    logger.info(
        "Discussion: fanpage %d created job %d (%s) label=%s subject=%r source=%s",
        fanpage.id, job.id, mode, copy.label, copy.subject_name,
        f"article:{article.id}" if article is not None else f"topic:{topic.id}",
    )

    # Auto mode → render now (staggered slightly); review mode waits for designer.
    if fanpage.discussion_publish_mode == PublishMode.auto:
        from app.tasks.design_renderer import render_discussion
        render_discussion.apply_async(args=[job.id], countdown=random.randint(5, 90))

    return True


@celery_app.task(name="app.tasks.discussion.generate_discussion_content")
def generate_discussion_content():
    """Beat tick: top up each Mode-4 fanpage toward its daily quota, paced across
    the active window. Creates at most one card per fanpage per tick so the
    output trickles rather than bursts."""
    db = SessionLocal()
    try:
        from sqlalchemy import func
        from app.models.target_fanpages import TargetFanpage
        from app.models.publish_jobs import PublishJob, ContentType

        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        now_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
        now_hour = now_wib.hour + now_wib.minute / 60.0
        if now_hour < _WINDOW_START_HOUR or now_hour >= _WINDOW_END_HOUR:
            return  # outside the active window — nothing to do

        day_start_utc, day_end_utc = _wib_day_bounds_utc(now_utc)

        fanpages = (
            db.query(TargetFanpage)
            .filter(
                TargetFanpage.discussion_enabled == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
                TargetFanpage.discussion_daily_count > 0,
            )
            .all()
        )

        created = 0
        for fp in fanpages:
            quota = fp.discussion_daily_count or 0
            count_today = (
                db.query(func.count(PublishJob.id))
                .filter(
                    PublishJob.fanpage_id == fp.id,
                    PublishJob.content_type == ContentType.discussion,
                    PublishJob.is_deleted == False,
                    PublishJob.created_at >= day_start_utc,
                    PublishJob.created_at < day_end_utc,
                )
                .scalar()
            ) or 0

            if count_today >= quota:
                continue
            if count_today >= _target_by_now(quota, now_hour):
                continue  # ahead of pace — wait for a later slot

            if _create_one(db, fp):
                created += 1

        if created:
            logger.info("Discussion sweep: created %d card(s) across %d fanpage(s)", created, len(fanpages))
    finally:
        db.close()
