"""Repliz Publisher task — sends posts to Facebook via Repliz API."""

import logging
import random
from datetime import datetime, timezone, timedelta

import pytz

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)
WIB = pytz.timezone("Asia/Jakarta")

_RETRY_BACKOFF = [300, 900, 2700]  # 5/15/45 minutes

# Gap between consecutive posts on the SAME fanpage (seconds) — randomized
# per post over a wide 5-35 min band (not a tight 10-20 min one) so the
# interval sequence itself doesn't read as a bot's suspiciously-consistent
# spacing. Separate from fan_out.py/news_copywriter.py's stagger, which only
# spaces the SAME content across DIFFERENT fanpages; this is what stops one
# fanpage's own feed from getting several unrelated posts in a burst (e.g. a
# source scrape returning 5 new articles at once). Average (~20 min) still
# comfortably clears _DEFAULT_DAILY_LIMIT posts within a day's non-sleep hours.
_MIN_POST_GAP_SECONDS = 300
_MAX_POST_GAP_SECONDS = 2100

# Same idea for breaking news, but much tighter — a human posting two
# separate breaking stories back to back would still leave a few minutes
# between them, not the full 5-35 min band a routine post uses (that would
# defeat the point of flagging something breaking in the first place).
# Triangular (not flat random.randint): a real editor reacts quickly most of
# the time and only occasionally takes closer to the full 10 min, so draws
# should cluster around the mode (7 min) and taper toward both edges — a flat
# distribution would land at the 5 min floor or 10 min ceiling just as often
# as anywhere in between, which reads as more mechanical than a person.
_BREAKING_MIN_GAP_SECONDS = 300   # 5 min
_BREAKING_MAX_GAP_SECONDS = 600   # 10 min
_BREAKING_GAP_MODE_SECONDS = 420  # 7 min — most draws cluster here

_DEFAULT_DAILY_LIMIT = 45


def _in_sleep_window(hour: int, start: int, end: int) -> bool:
    if start == end:
        return False  # degenerate config — treat as disabled
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end  # wraps past midnight, e.g. 22 -> 6


def _push_past_sleep(dt_wib: datetime, start: int, end: int) -> datetime:
    """dt_wib is WIB-aware. Returns the next moment >= dt_wib that's outside
    the [start, end) sleep window (rolling to end:00, next day if already past)."""
    if not _in_sleep_window(dt_wib.hour, start, end):
        return dt_wib
    target = dt_wib.replace(hour=end, minute=0, second=0, microsecond=0)
    if target <= dt_wib:
        target += timedelta(days=1)
    return target


_MAX_DAY_HOPS = 60  # bounded — even a multi-week backlog resolves well inside this


def _next_schedule_at(db, fanpage_id: int, breaking: bool = False) -> datetime:
    """This fanpage's next Facebook go-live slot: the EARLIEST WIB day (starting
    from now) that still has room under the daily cap, with the actual time
    spaced a random 10-20 min gap after THAT DAY's own latest scheduled post,
    never inside the WIB sleep window.

    Deliberately scoped per-day rather than chained off the fanpage's single
    all-time latest scheduled_for (the old behavior): a burst of renders
    finishing in a short window (e.g. a lot of source posts on the same day)
    used to push every later post's slot forward from wherever that burst's
    tail landed — permanently, since the next call always anchored off that
    same ever-advancing marker regardless of how much spare capacity a later,
    quieter day still had under its own cap. That turned one busy day into a
    multi-day publish lag with no way to self-correct. Scanning day-by-day and
    anchoring the gap to each day's own last post lets a lower-volume day
    absorb the catch-up instead of the backlog only ever growing.

    `breaking=True` (see PublishJob.is_breaking / news_copywriter's
    is_breaking classification) skips the daily-cap check and day-hopping —
    and goes out as close to "now" as the fanpage's sleep window allows. The
    point of flagging something breaking is that it's time-sensitive; queuing
    it behind the normal pacing logic (which can legitimately push a post out
    by hours or into the next day once a fanpage's daily cap is hit) defeats
    that entirely. Still respects the sleep window — even breaking news
    shouldn't post at 3am to an inactive audience.

    It does still enforce one thing: a short minimum gap
    (_BREAKING_MIN_GAP_SECONDS-_BREAKING_MAX_GAP_SECONDS, ~5-10 min) since
    this SAME fanpage's own most recent scheduled post (breaking or not).
    Found 2026-08-23 via a real user report: two separate breaking stories
    landing on the same fanpage within seconds of each other reads as an
    obvious bot, even though each one individually deserves to skip the
    normal queue — "time-sensitive" doesn't mean "simultaneous with this
    page's last post." Unlike the normal path's day-scoped gap check, this
    looks at the fanpage's all-time latest scheduled_for (a second breaking
    story minutes after the first, possibly crossing a WIB midnight, should
    still respect the gap)."""
    from app.models.target_fanpages import TargetFanpage

    fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    sleep_start = fanpage.publish_sleep_start_hour if fanpage else None
    sleep_end = fanpage.publish_sleep_end_hour if fanpage else None
    floor_utc = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60)

    if breaking:
        from sqlalchemy import func
        from app.models.publish_jobs import PublishJob

        last_scheduled = (
            db.query(func.max(PublishJob.scheduled_for))
            .filter(PublishJob.fanpage_id == fanpage_id, PublishJob.scheduled_for.isnot(None))
            .scalar()
        )
        candidate_utc = floor_utc
        if last_scheduled:
            gap = timedelta(seconds=random.triangular(
                _BREAKING_MIN_GAP_SECONDS, _BREAKING_MAX_GAP_SECONDS, _BREAKING_GAP_MODE_SECONDS,
            ))
            candidate_utc = max(candidate_utc, last_scheduled + gap)
        if sleep_start is not None and sleep_end is not None:
            candidate_wib = candidate_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
            pushed_wib = _push_past_sleep(candidate_wib, sleep_start, sleep_end)
            return pushed_wib.astimezone(timezone.utc).replace(tzinfo=None)
        return candidate_utc

    from sqlalchemy import func
    from app.models.publish_jobs import PublishJob

    daily_limit = (fanpage.publish_daily_limit if fanpage else None) or _DEFAULT_DAILY_LIMIT
    day_wib = floor_utc.replace(tzinfo=timezone.utc).astimezone(WIB)

    for _ in range(_MAX_DAY_HOPS):
        day_start_wib = day_wib.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_wib = day_start_wib + timedelta(days=1)
        day_start_utc = day_start_wib.astimezone(timezone.utc).replace(tzinfo=None)
        day_end_utc = day_end_wib.astimezone(timezone.utc).replace(tzinfo=None)

        count_today, last_today = (
            db.query(func.count(PublishJob.id), func.max(PublishJob.scheduled_for))
            .filter(
                PublishJob.fanpage_id == fanpage_id,
                PublishJob.scheduled_for >= day_start_utc,
                PublishJob.scheduled_for < day_end_utc,
            )
            .one()
        )
        if count_today >= daily_limit:
            day_wib = day_end_wib
            continue

        gap = timedelta(seconds=random.randint(_MIN_POST_GAP_SECONDS, _MAX_POST_GAP_SECONDS))
        earliest_today_utc = max(floor_utc, day_start_utc)
        candidate_utc = max(earliest_today_utc, last_today + gap) if last_today else earliest_today_utc

        if candidate_utc >= day_end_utc:
            day_wib = day_end_wib
            continue

        if sleep_start is not None and sleep_end is not None:
            candidate_wib = candidate_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
            pushed_wib = _push_past_sleep(candidate_wib, sleep_start, sleep_end)
            if pushed_wib != candidate_wib:
                pushed_utc = pushed_wib.astimezone(timezone.utc).replace(tzinfo=None)
                if pushed_utc >= day_end_utc:
                    day_wib = day_end_wib
                    continue
                candidate_utc = pushed_utc

        return candidate_utc

    # Pathological backlog (>60 days deep) — fall back to "now" rather than loop forever.
    return floor_utc


@celery_app.task(name="app.tasks.publisher.publish_job", bind=True, max_retries=3)
def publish_job(self, job_id: int):
    """Publish a single PublishJob to Repliz."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus
        from app.models.target_fanpages import TargetFanpage
        from app.models.posts import Post, PostStatus
        from app.services.repliz_client import get_repliz_client_from_db

        # Atomic claim: pending_publish -> publishing. Guards against the same
        # job being sent to Repliz twice — e.g. a slow render dispatching
        # publish_job twice (see design_renderer.render_design), a manual
        # "Publish" double-click, or a Celery redelivery after the DB commit
        # was lost. A second concurrent call sees 0 rows updated and exits.
        claimed = (
            db.query(PublishJob)
            .filter(PublishJob.id == job_id, PublishJob.status == PublishJobStatus.pending_publish)
            .update({"status": PublishJobStatus.publishing}, synchronize_session=False)
        )
        db.commit()
        if not claimed:
            return

        job = db.query(PublishJob).filter_by(id=job_id).first()

        from app.models.publish_jobs import ContentType
        # news_content, ig_recreate, discussion, and pinterest_content all
        # publish a single rendered design PNG via the same path
        # (design_image_url + ai_generated_caption).
        if job.content_type in (ContentType.news_content, ContentType.ig_recreate, ContentType.discussion, ContentType.pinterest_content):
            _publish_news_job(db, job)
            return

        post = db.query(Post).filter_by(id=job.post_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()

        if not post.image_public_urls:
            logger.error("Job %d: no public image URLs available", job_id)
            job.status = PublishJobStatus.failed
            job.last_error = "No public image URLs"
            db.commit()
            return

        if job.watermarked_image_urls:
            image_urls = list(job.watermarked_image_urls)
        else:
            # Use original IG CDN URLs when public URLs are localhost (dev environment)
            pub_urls = list(post.image_public_urls)
            if pub_urls and "localhost" in pub_urls[0] and post.image_source_urls:
                image_urls = list(post.image_source_urls)
                logger.info("Job %d: using IG source URLs (public URLs are localhost)", job_id)
            else:
                image_urls = pub_urls

        client = get_repliz_client_from_db(db)
        from app.services.repliz_client import format_schedule_at

        caption = job.ai_generated_caption or ""
        scheduled_for = _next_schedule_at(db, job.fanpage_id)
        schedule_at = format_schedule_at(scheduled_for)

        if post.media_type == "album" and len(image_urls) >= 2:
            response = client.create_album_schedule(
                account_id=fanpage.repliz_account_id,
                description=caption,
                image_urls=image_urls,
                schedule_at=schedule_at,
            )
        else:
            response = client.create_image_schedule(
                account_id=fanpage.repliz_account_id,
                description=caption,
                image_url=image_urls[0],
                schedule_at=schedule_at,
            )

        schedule_id = response.get("_id") or response.get("id") or response.get("scheduleId")

        now = datetime.now(timezone.utc)
        job.repliz_schedule_id = schedule_id
        job.repliz_response_json = response
        job.status = PublishJobStatus.published
        job.published_at = now
        job.scheduled_for = scheduled_for
        job.cleanup_at = now + timedelta(hours=6)
        job.attempt_count = (job.attempt_count or 0) + 1
        db.commit()

        # Check if ALL jobs for the post are published → mark post done
        _maybe_mark_post_done(db, post)

        logger.info(
            "Job %d published to fanpage '%s' via Repliz (schedule=%s)",
            job_id, fanpage.name, schedule_id,
        )

    except Exception as exc:
        db.rollback()
        attempt = self.request.retries
        countdown = _RETRY_BACKOFF[min(attempt, len(_RETRY_BACKOFF) - 1)]

        logger.error(
            "Publish failed for job %d (attempt %d/%d): %s",
            job_id, attempt + 1, self.max_retries, exc,
        )

        try:
            from app.models.publish_jobs import PublishJob, PublishJobStatus
            job = db.query(PublishJob).filter_by(id=job_id).first()
            if job:
                job.attempt_count = (job.attempt_count or 0) + 1
                job.last_error = str(exc)
                if attempt + 1 >= self.max_retries:
                    job.status = PublishJobStatus.failed
                elif job.status == PublishJobStatus.publishing:
                    # Release the claim so the retry's atomic claim can
                    # succeed again — otherwise it'd find status != pending_publish
                    # and silently no-op every retry attempt.
                    job.status = PublishJobStatus.pending_publish
                db.commit()
        except Exception:
            db.rollback()

        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()


def _publish_news_job(db, job):
    """Publish a news_content job (Feature 2, Phase 2E): single image post
    whose media is the rendered design PNG. No Post row involved."""
    from app.models.publish_jobs import PublishJobStatus, ContentType
    from app.models.target_fanpages import TargetFanpage
    from app.models.scraped_articles import ScrapedArticle, ArticleStatus
    from app.services.repliz_client import get_repliz_client_from_db

    fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()

    if not job.design_image_url:
        job.status = PublishJobStatus.failed
        job.last_error = "No rendered design image"
        db.commit()
        logger.error("Job %d: news job has no design_image_url", job.id)
        return

    if "localhost" in job.design_image_url or "127.0.0.1" in job.design_image_url:
        # Repliz fetches medias server-side — a localhost URL would create a
        # broken schedule against the real fanpage. Hold at pending_publish
        # (release the 'publishing' claim so a later manual/auto retry can
        # actually re-claim it instead of finding it stuck).
        job.status = PublishJobStatus.pending_publish
        job.last_error = (
            "design image URL is localhost — not reachable by Repliz. "
            "Serve media from a public URL (VPS / tunnel) to publish."
        )
        db.commit()
        logger.warning("Job %d: holding news publish — design_image_url is localhost", job.id)
        return

    client = get_repliz_client_from_db(db)
    from app.services.repliz_client import format_schedule_at

    scheduled_for = _next_schedule_at(db, job.fanpage_id, breaking=job.is_breaking)
    response = client.create_image_schedule(
        account_id=fanpage.repliz_account_id,
        description=job.ai_generated_caption or "",
        image_url=job.design_image_url,
        alt=job.design_title or "",
        schedule_at=format_schedule_at(scheduled_for),
    )

    schedule_id = response.get("_id") or response.get("id") or response.get("scheduleId")

    now = datetime.now(timezone.utc)
    job.repliz_schedule_id = schedule_id
    job.repliz_response_json = response
    job.status = PublishJobStatus.published
    job.published_at = now
    job.scheduled_for = scheduled_for
    job.cleanup_at = now + timedelta(hours=6)
    job.attempt_count = (job.attempt_count or 0) + 1

    if job.source_article_id:
        article = db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()
        if article:
            article.status = ArticleStatus.published

    if job.content_type == ContentType.pinterest_content and job.source_gallery_image_id:
        # Mode 5: once an idea's post actually goes live, it has no further
        # use sitting in the queue (the queue is for reviewing/editing
        # ideas BEFORE they post, not a history log — History already
        # covers that) — delete it rather than accumulate months of
        # "used" rows behind the pending ones (flagged by the user: ~400
        # ideas/month at scale). Matched via the shared GalleryImage since
        # each pin is only ever ingested into one idea (source_image_url
        # dedup), so this is a safe 1:1 lookup without a separate FK.
        from app.models.pinterest_content_ideas import PinterestContentIdea
        db.query(PinterestContentIdea).filter_by(
            gallery_image_id=job.source_gallery_image_id, status="used",
        ).delete(synchronize_session=False)

    db.commit()
    logger.info(
        "Job %d (news) published to fanpage '%s' via Repliz (schedule=%s)",
        job.id, fanpage.name, schedule_id,
    )


def _maybe_mark_post_done(db, post):
    """Mark post as done if all its publish_jobs are terminal states."""
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.posts import Post, PostStatus

    non_terminal = (
        db.query(PublishJob)
        .filter(
            PublishJob.post_id == post.id,
            PublishJob.status.in_([
                PublishJobStatus.pending_caption,
                PublishJobStatus.pending_review,
                PublishJobStatus.pending_publish,
            ]),
        )
        .count()
    )

    if non_terminal == 0:
        post.status = PostStatus.done
        db.commit()


@celery_app.task(name="app.tasks.publisher.recover_stuck_auto_publishes")
def recover_stuck_auto_publishes():
    """Re-trigger publish_job for pending_publish jobs whose fanpage is set
    to auto — catches Mode 4 discussion cards (and Mode 2 news, same gap)
    that rendered successfully while the fanpage was still manual_review and
    have sat waiting for a human "Publish" click ever since, even after an
    admin later flips the fanpage to auto.

    render_discussion/render_design only check discussion_publish_mode /
    mode2_publish_mode ONCE, at the moment rendering finishes — nothing else
    ever revisits an already-rendered pending_publish job, so a publish-mode
    change never applies retroactively to a queue that built up under the
    old setting. Found 2026-08-20: 7 discussion cards for two fanpages sat
    at pending_publish for 1-2 days after being switched to auto, because
    only newly-rendered cards from that point on were auto-publishing.

    Safe to run frequently — publish_job's own atomic pending_publish→
    publishing claim means a job already mid-publish (or already picked up
    by this same sweep concurrently) is just a no-op the second time."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode

        discussion_jobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_publish,
                PublishJob.content_type == ContentType.discussion,
                TargetFanpage.discussion_publish_mode == PublishMode.auto,
            )
            .all()
        )
        news_jobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_publish,
                PublishJob.content_type == ContentType.news_content,
                TargetFanpage.mode2_publish_mode == PublishMode.auto,
            )
            .all()
        )
        pinterest_jobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_publish,
                PublishJob.content_type == ContentType.pinterest_content,
                TargetFanpage.pinterest_publish_mode == PublishMode.auto,
            )
            .all()
        )
        job_ids = [j for (j,) in discussion_jobs] + [j for (j,) in news_jobs] + [j for (j,) in pinterest_jobs]
        for job_id in job_ids:
            publish_job.delay(job_id)
        if job_ids:
            logger.info("Recovery: re-dispatched %d pending_publish job(s) stuck under a now-auto fanpage", len(job_ids))
    finally:
        db.close()
