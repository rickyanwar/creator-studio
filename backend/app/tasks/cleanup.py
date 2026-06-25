"""Cleanup task — deletes media files 4 days after all jobs are published."""

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="app.tasks.cleanup.cleanup_old_media")
def cleanup_old_media():
    """Remove media files for posts where all jobs are published and cleanup_at has passed."""
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    cleaned = 0

    try:
        from app.models.posts import Post, PostStatus
        from app.models.publish_jobs import PublishJob, PublishJobStatus

        # Find posts ready for cleanup
        posts_to_clean = (
            db.query(Post)
            .filter(
                Post.status == PostStatus.done,
                Post.image_local_paths != "{}",
            )
            .all()
        )

        for post in posts_to_clean:
            # Ensure all jobs are in terminal states and past cleanup_at
            jobs = db.query(PublishJob).filter_by(post_id=post.id).all()
            if not jobs:
                continue

            all_done = all(
                j.status in (PublishJobStatus.published, PublishJobStatus.failed, PublishJobStatus.skipped)
                for j in jobs
            )
            cleanup_due = all(
                j.cleanup_at and j.cleanup_at <= now
                for j in jobs
                if j.status == PublishJobStatus.published
            )

            if not (all_done and cleanup_due):
                continue

            # Delete the post directory
            post_dir = Path(settings.storage_base_path) / "posts" / str(post.uuid)
            if post_dir.exists():
                try:
                    shutil.rmtree(post_dir)
                    logger.info("Cleaned media for post %d (%s)", post.id, post.uuid)
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
