"""IG Crawler task — runs every 30 minutes with jitter.

Anti-ban rules:
- Sleep window 01:00-06:00 WIB: skip entirely.
- Delay 30-90s random between each burner.
- Max 200 req/day per burner.
- Jitter ±5 min built in via Celery countdown randomisation.
"""

import logging
import random
import time
from datetime import datetime, timezone

import pytz

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
WIB = pytz.timezone("Asia/Jakarta")


def _in_sleep_window() -> bool:
    """Return True if current WIB time is inside the no-crawl window."""
    now_wib = datetime.now(WIB)
    hour = now_wib.hour
    return settings.crawl_sleep_start_wib <= hour < settings.crawl_sleep_end_wib


@celery_app.task(name="app.tasks.crawler.crawl_all_sources", bind=True, max_retries=0)
def crawl_all_sources(self):
    """Main crawler task — iterates all active IG sources."""
    if _in_sleep_window():
        logger.info("Crawler skipped: sleep window active (WIB %02d:00-%02d:00)",
                    settings.crawl_sleep_start_wib, settings.crawl_sleep_end_wib)
        return

    db = SessionLocal()
    try:
        from app.models.ig_sources import IGSource
        from app.models.fanpage_sources import FanpageSource

        # Only crawl sources that have at least one active fanpage link
        sources = (
            db.query(IGSource)
            .join(FanpageSource, FanpageSource.ig_source_id == IGSource.id)
            .filter(IGSource.is_active == True, FanpageSource.is_active == True)
            .distinct()
            .all()
        )

        logger.info("Crawling %d active IG sources", len(sources))

        for source in sources:
            # Jitter between sources
            delay = random.uniform(30, 90)
            logger.debug("Waiting %.1fs before crawling @%s", delay, source.ig_username)
            time.sleep(delay)
            crawl_single_source.delay(source.id)

    finally:
        db.close()


@celery_app.task(name="app.tasks.crawler.crawl_single_source", bind=True, max_retries=2)
def crawl_single_source(self, source_id: int):
    """Crawl one IG source and enqueue image-save for any new posts."""
    db = SessionLocal()
    try:
        from app.models.ig_sources import IGSource
        from app.models.burner_accounts import BurnerAccount, BurnerStatus
        from app.models.posts import Post, MediaType, PostStatus
        from app.services.ig_session_manager import IGSessionManager

        source = db.query(IGSource).filter_by(id=source_id).first()
        if not source or not source.is_active:
            return

        # Pick a random active burner that hasn't hit the daily limit
        available = (
            db.query(BurnerAccount)
            .filter(
                BurnerAccount.status == BurnerStatus.active,
                BurnerAccount.requests_today < 200,
            )
            .all()
        )
        if not available:
            logger.warning("No available burners to crawl @%s — all busy or at limit", source.ig_username)
            return

        burner = random.choice(available)

        manager = IGSessionManager(burner, db)
        medias = manager.fetch_recent_posts(source.ig_username, amount=12)

        new_count = 0
        for media in medias:
            ig_media_id = str(media.pk)

            # Skip already-seen posts
            if db.query(Post).filter_by(ig_media_id=ig_media_id).first():
                continue

            # Smart adaptive carousel logic
            resources = getattr(media, "resources", []) or []
            images_in_post = [r for r in resources if getattr(r, "media_type", None) == 1]  # 1 = IMAGE

            if media.media_type == 8:  # ALBUM
                image_count = len(images_in_post) if images_in_post else len(resources)
                if image_count == 0:
                    logger.debug("Skipping video-only album from @%s", source.ig_username)
                    continue
                media_type_enum = MediaType.album if image_count >= 2 else MediaType.image
            elif media.media_type == 1:  # IMAGE
                media_type_enum = MediaType.image
            else:
                # VIDEO or REEL — skip
                continue

            post = Post(
                ig_source_id=source.id,
                ig_media_id=ig_media_id,
                ig_post_url=f"https://www.instagram.com/p/{media.code}/",
                media_type=media_type_enum,
                original_caption=media.caption_text or "",
                taken_at=media.taken_at,
                status=PostStatus.crawled,
            )
            db.add(post)
            db.flush()  # get post.id before commit
            new_count += 1

            # Update last_seen_post_id to most recent
            if not source.last_seen_post_id:
                source.last_seen_post_id = ig_media_id

            # Enqueue image download, filtering by per-source album_image_indices
            from app.tasks.image_saver import save_post_images
            all_urls = _extract_image_urls(media)
            if media_type_enum == MediaType.album:
                indices = source.album_image_indices or [1]
                image_urls = [all_urls[i - 1] for i in indices if 1 <= i <= len(all_urls)]
                if not image_urls:
                    image_urls = all_urls[:1]
            else:
                image_urls = all_urls
            db.commit()
            save_post_images.delay(post.id, image_urls)

        source.last_checked_at = datetime.now(timezone.utc)
        burner.requests_today = (burner.requests_today or 0) + 1
        db.commit()

        logger.info("@%s: found %d new posts", source.ig_username, new_count)

    except Exception as exc:
        db.rollback()
        logger.error("Error crawling @%s: %s", source_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


def _extract_image_urls(media) -> list[str]:
    """Extract all image URLs from a media object."""
    resources = getattr(media, "resources", []) or []
    if resources:
        urls = []
        for r in resources:
            url = getattr(r, "thumbnail_url", None) or getattr(r, "url", None)
            if url:
                urls.append(str(url))
        if urls:
            return urls

    # Single image
    url = getattr(media, "thumbnail_url", None) or getattr(media, "url", None)
    return [str(url)] if url else []


@celery_app.task(name="app.tasks.crawler.reset_burner_request_counters")
def reset_burner_request_counters():
    """Reset requests_today counter for all burners at midnight WIB."""
    db = SessionLocal()
    try:
        from app.models.burner_accounts import BurnerAccount
        db.query(BurnerAccount).update({BurnerAccount.requests_today: 0})
        db.commit()
        logger.info("Burner request counters reset")
    finally:
        db.close()
