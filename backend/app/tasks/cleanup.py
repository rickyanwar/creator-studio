"""Cleanup task — deletes media files after publishing or when stuck."""

import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Delete files from stuck/orphaned posts after this long regardless
_STUCK_POST_MAX_AGE_HOURS = 12


@celery_app.task(name="app.tasks.cleanup.cleanup_old_media")
def cleanup_old_media():
    """Remove media files for:
    - Posts where all jobs published and cleanup_at passed (6h after publish)
    - Posts where all jobs failed/skipped (no point keeping files)
    - Posts stuck in stored/pending_fanout for more than 12h
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    cleaned = 0

    try:
        from app.models.posts import Post, PostStatus
        from app.models.publish_jobs import PublishJob, PublishJobStatus

        candidates = (
            db.query(Post)
            .filter(
                Post.status.in_([PostStatus.done, PostStatus.stored, PostStatus.pending_fanout]),
                Post.image_local_paths != "{}",
            )
            .all()
        )

        for post in candidates:
            jobs = db.query(PublishJob).filter_by(post_id=post.id).all()
            should_clean = False

            if post.status == PostStatus.done:
                # All jobs terminal + cleanup_at passed for published ones
                all_terminal = all(
                    j.status in (PublishJobStatus.published, PublishJobStatus.failed, PublishJobStatus.skipped)
                    for j in jobs
                )
                published_jobs = [j for j in jobs if j.status == PublishJobStatus.published]
                cleanup_due = all(j.cleanup_at and j.cleanup_at <= now for j in published_jobs)
                should_clean = all_terminal and (not published_jobs or cleanup_due)

            elif jobs and all(
                j.status in (PublishJobStatus.failed, PublishJobStatus.skipped) for j in jobs
            ):
                # All jobs failed/skipped — files will never be used
                should_clean = True

            elif post.crawled_at and (now - post.crawled_at.replace(tzinfo=timezone.utc)).total_seconds() > _STUCK_POST_MAX_AGE_HOURS * 3600:
                # Stuck in stored/pending_fanout for too long — orphaned
                should_clean = True

            if not should_clean:
                continue

            post_dir = Path(settings.storage_base_path) / "posts" / str(post.uuid)
            if post_dir.exists():
                try:
                    shutil.rmtree(post_dir)
                    logger.info("Cleaned media for post %d (%s) [status=%s]", post.id, post.uuid, post.status)
                except OSError as exc:
                    logger.error("Failed to delete %s: %s", post_dir, exc)
                    continue

            post.image_local_paths = []
            post.image_public_urls = []
            post.status = PostStatus.cleaned
            db.add(post)
            cleaned += 1

        db.commit()
        logger.info("Cleanup complete: %d posts cleaned", cleaned)

    except Exception as exc:
        db.rollback()
        logger.error("Cleanup task error: %s", exc, exc_info=True)
    finally:
        db.close()
