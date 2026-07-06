"""Image Saver task — download IG images to VPS storage."""

import logging
import os
from pathlib import Path

import httpx

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="app.tasks.image_saver.save_post_images", bind=True, max_retries=3)
def save_post_images(self, post_id: int, image_urls: list[str]):
    """Download image URLs and store them under /var/www/media/posts/{post_uuid}/."""
    db = SessionLocal()
    try:
        from app.models.posts import Post, PostStatus

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            logger.error("Post %d not found", post_id)
            return

        post_dir = Path(settings.storage_base_path) / "posts" / str(post.uuid)
        post_dir.mkdir(parents=True, exist_ok=True)

        local_paths = []
        public_urls = []

        for idx, url in enumerate(image_urls):
            filename = f"{idx}.jpg"
            local_path = post_dir / filename

            try:
                _download_image(url, local_path)
            except Exception as exc:
                logger.error("Failed to download image %s: %s", url, exc)
                continue

            local_paths.append(str(local_path))
            public_url = f"{settings.storage_base_url.rstrip('/')}/posts/{post.uuid}/{filename}"
            public_urls.append(public_url)

        if not local_paths:
            logger.error("No images saved for post %d — all downloads failed", post_id)
            return

        post.image_local_paths = local_paths
        post.image_public_urls = public_urls
        post.image_source_urls = image_urls  # original IG CDN URLs (used when public_urls are localhost)

        from app.models.ig_sources import IGSource
        ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first()

        if ig_source and ig_source.image_edit_enabled:
            post.status = PostStatus.editing_image
            db.commit()

            from app.services.ai_image_edit import clean_and_translate_image

            try:
                for local_path in local_paths:
                    with open(local_path, "rb") as f:
                        original_bytes = f.read()
                    edited_bytes = clean_and_translate_image(original_bytes, ig_source.image_edit_custom_prompt)
                    with open(local_path, "wb") as f:
                        f.write(edited_bytes)
            except Exception as exc:
                # Image edit quota/rate limits are stricter than download failures —
                # hold the post at 'editing_image' and back off longer before retrying.
                logger.warning("Post %d: image edit failed, will retry: %s", post_id, exc)
                raise self.retry(exc=exc, countdown=300, max_retries=8)

            logger.info("Post %d: cleaned %d images", post_id, len(local_paths))

        post.status = PostStatus.stored
        db.commit()

        logger.info("Post %d: saved %d images to %s", post_id, len(local_paths), post_dir)

        # Trigger fan-out
        from app.tasks.fan_out import create_fanout_jobs
        create_fanout_jobs.delay(post_id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Error saving images for post %d: %s", post_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(name="app.tasks.image_saver.recover_stuck_image_edits")
def recover_stuck_image_edits():
    """Re-trigger image editing for posts stuck in 'editing_image' too long.

    Runs every 30 minutes to catch cases where the edit retries were
    exhausted or the task was dropped from Redis mid-chain.
    """
    from datetime import datetime, timezone, timedelta

    db = SessionLocal()
    try:
        from app.models.posts import Post, PostStatus

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

        stuck_posts = (
            db.query(Post)
            .filter(
                Post.status == PostStatus.editing_image,
                Post.updated_at < cutoff,
            )
            .all()
        )

        for post in stuck_posts:
            save_post_images.delay(post.id, list(post.image_source_urls))
            logger.warning("Recovery: re-queued image edit for stuck post %d", post.id)

    finally:
        db.close()


def _download_image(url: str, dest: Path, timeout: int = 30):
    """Download an image URL and save to dest path."""
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)",
    }
    with httpx.stream("GET", url, headers=headers, timeout=timeout, follow_redirects=True) as resp:
        resp.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=8192):
                f.write(chunk)
