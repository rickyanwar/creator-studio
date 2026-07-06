"""Repliz Publisher task — sends posts to Facebook via Repliz API."""

import logging
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

logger = logging.getLogger(__name__)

_RETRY_BACKOFF = [300, 900, 2700]  # 5/15/45 minutes


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

        caption = job.ai_generated_caption or ""

        if post.media_type == "album" and len(image_urls) >= 2:
            response = client.create_album_schedule(
                account_id=fanpage.repliz_account_id,
                description=caption,
                image_urls=image_urls,
            )
        else:
            response = client.create_image_schedule(
                account_id=fanpage.repliz_account_id,
                description=caption,
                image_url=image_urls[0],
            )

        schedule_id = response.get("_id") or response.get("id") or response.get("scheduleId")

        now = datetime.now(timezone.utc)
        job.repliz_schedule_id = schedule_id
        job.repliz_response_json = response
        job.status = PublishJobStatus.published
        job.published_at = now
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
