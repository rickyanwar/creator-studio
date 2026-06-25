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
        post.status = PostStatus.stored
        db.commit()

        logger.info("Post %d: saved %d images to %s", post_id, len(local_paths), post_dir)

        # Trigger fan-out
        from app.tasks.fan_out import create_fanout_jobs
        create_fanout_jobs.delay(post_id)

    except Exception as exc:
        db.rollback()
        logger.error("Error saving images for post %d: %s", post_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
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
