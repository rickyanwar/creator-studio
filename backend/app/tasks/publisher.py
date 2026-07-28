"""Repliz Publisher task — sends posts to Facebook via Repliz API."""

import logging
import random
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_RETRY_BACKOFF = [300, 900, 2700]  # 5/15/45 minutes

# Minimum gap between consecutive posts on the SAME fanpage (seconds) — 10 to
# 20 minutes, randomized per post. Separate from fan_out.py/news_copywriter.py's
# stagger, which only spaces the SAME content across DIFFERENT fanpages; this
# is what stops one fanpage's own feed from getting several unrelated posts
# in a burst (e.g. a source scrape returning 5 new articles at once).
_MIN_POST_GAP_SECONDS = 600
_MAX_POST_GAP_SECONDS = 1200


def _next_schedule_at(db, fanpage_id: int) -> datetime:
    """This fanpage's next Facebook go-live slot: never earlier than now+60s,
    and never closer than a random 10-20 min gap after its own last scheduled
    post (scheduled_for, not published_at — that's when we called the API,
    not the actual future slot, which may itself have been pushed out)."""
    from sqlalchemy import func
    from app.models.publish_jobs import PublishJob

    floor = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=60)
    last = (
        db.query(func.max(PublishJob.scheduled_for))
        .filter(PublishJob.fanpage_id == fanpage_id)
        .scalar()
    )
    if not last:
        return floor
    gap = timedelta(seconds=random.randint(_MIN_POST_GAP_SECONDS, _MAX_POST_GAP_SECONDS))
    return max(floor, last + gap)


@celery_app.task(name="app.tasks.publisher.publish_job", bind=True, max_retries=3)
def publish_job(self, job_id: int):
    """Publish a single PublishJob to Repliz."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus
        from app.models.target_fanpages import TargetFanpage
        from app.models.posts import Post, PostStatus
        from app.services.repliz_client import get_repliz_client_from_db

        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job or job.status != PublishJobStatus.pending_publish:
            return

        from app.models.publish_jobs import ContentType
        # news_content and ig_recreate both publish a single rendered design PNG
        if job.content_type in (ContentType.news_content, ContentType.ig_recreate):
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
                db.commit()
        except Exception:
            db.rollback()

        raise self.retry(exc=exc, countdown=countdown)
    finally:
        db.close()


def _publish_news_job(db, job):
    """Publish a news_content job (Feature 2, Phase 2E): single image post
    whose media is the rendered design PNG. No Post row involved."""
    from app.models.publish_jobs import PublishJobStatus
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
        # broken schedule against the real fanpage. Hold at pending_publish.
        job.last_error = (
            "design image URL is localhost — not reachable by Repliz. "
            "Serve media from a public URL (VPS / tunnel) to publish."
        )
        db.commit()
        logger.warning("Job %d: holding news publish — design_image_url is localhost", job.id)
        return

    client = get_repliz_client_from_db(db)
    from app.services.repliz_client import format_schedule_at

    scheduled_for = _next_schedule_at(db, job.fanpage_id)
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
