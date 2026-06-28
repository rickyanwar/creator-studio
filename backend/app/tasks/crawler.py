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
from datetime import datetime, timezone, timedelta

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
def crawl_all_sources(self, manual: bool = False):
    """Main crawler task — iterates all active IG sources."""
    if not manual and _in_sleep_window():
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

        logger.info("Crawling %d active IG sources (manual=%s)", len(sources), manual)

        for source in sources:
            if not manual:
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
        from app.models.ig_sources import IGSource, ScraperBackend
        from app.models.burner_accounts import BurnerAccount, BurnerStatus
        from app.models.posts import Post, MediaType, PostStatus
        from app.services.ig_session_manager import IGSessionManager

        source = db.query(IGSource).filter_by(id=source_id).first()
        if not source or not source.is_active:
            return

        backend = source.scraper_backend or ScraperBackend.auto
        fetch_amount = random.randint(9, 15)
        medias = _fetch_medias(source, backend, db, fetch_amount)

        new_count = 0
        from app.models.settings import Settings as DBSettings
        db_settings = db.query(DBSettings).filter_by(id=1).first()
        max_age_days = db_settings.max_post_age_days if db_settings and db_settings.max_post_age_days else settings.max_post_age_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        for media in medias:
            ig_media_id = str(media.pk)

            # Skip already-seen posts
            if db.query(Post).filter_by(ig_media_id=ig_media_id).first():
                continue

            # Skip posts older than max_post_age_days
            taken_at = getattr(media, "taken_at", None)
            if taken_at:
                if taken_at.tzinfo is None:
                    taken_at = taken_at.replace(tzinfo=timezone.utc)
                if taken_at < cutoff:
                    logger.debug("Skipping old post %s from @%s (taken %s)", ig_media_id, source.ig_username, taken_at.date())
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

            caption_raw = getattr(media, "caption_text", None) or ""
            if isinstance(caption_raw, dict):
                caption_raw = caption_raw.get("text", "") or ""
            post = Post(
                ig_source_id=source.id,
                ig_media_id=ig_media_id,
                ig_post_url=f"https://www.instagram.com/p/{media.code}/",
                media_type=media_type_enum,
                original_caption=str(caption_raw),
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
        db.commit()

        logger.info("@%s: found %d new posts", source.ig_username, new_count)

    except Exception as exc:
        db.rollback()
        logger.error("Error crawling @%s: %s", source_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


def _resolve_effective_backend(source_backend, db):
    """Return the effective ScraperBackend, applying the global scraper_mode override."""
    from app.models.ig_sources import ScraperBackend
    from app.models.settings import Settings as DBSettings

    db_settings = db.query(DBSettings).filter_by(id=1).first()
    global_mode = (db_settings.scraper_mode if db_settings and db_settings.scraper_mode else "auto")

    if global_mode == "flashapi":
        return ScraperBackend.flashapi
    if global_mode == "instagrapi":
        return ScraperBackend.instagrapi
    # "auto" → honour per-source setting
    return source_backend or ScraperBackend.auto


def _get_flashapi_key(db) -> str:
    """Return the FlashAPI key from DB (preferred) or env fallback."""
    from app.models.settings import Settings as DBSettings
    from app.services.encryption import decrypt

    db_settings = db.query(DBSettings).filter_by(id=1).first()
    if db_settings and db_settings.flashapi_api_key_encrypted:
        try:
            return decrypt(db_settings.flashapi_api_key_encrypted)
        except Exception:
            pass
    return settings.flashapi_api_key  # env fallback


def _fetch_medias(source, backend, db, amount: int) -> list:
    """Fetch recent posts using the appropriate backend for this source.

    Global scraper_mode in DB Settings overrides the per-source backend.
    Falls back to FlashAPI (auto mode) when no burner is available.
    """
    from app.models.ig_sources import ScraperBackend
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.services.ig_session_manager import IGSessionManager

    effective = _resolve_effective_backend(backend, db)
    use_flashapi = effective == ScraperBackend.flashapi

    if effective in (ScraperBackend.auto, ScraperBackend.instagrapi):
        burner = None
        if source.burner_account_id:
            assigned = db.query(BurnerAccount).filter_by(id=source.burner_account_id).first()
            if assigned and assigned.status == BurnerStatus.active and assigned.requests_today < 200:
                burner = assigned

        if burner is None:
            available = (
                db.query(BurnerAccount)
                .filter(
                    BurnerAccount.status == BurnerStatus.active,
                    BurnerAccount.requests_today < 200,
                )
                .all()
            )
            if available:
                burner = random.choice(available)
                source.burner_account_id = burner.id
                db.commit()
                logger.info("Re-assigned @%s to burner @%s", source.ig_username, burner.ig_username)
            elif effective == ScraperBackend.instagrapi:
                logger.warning("No available burners to crawl @%s — all busy or at limit", source.ig_username)
                return []
            else:
                # auto mode: no burner → fall back to FlashAPI
                logger.info("No burner available for @%s — trying FlashAPI fallback", source.ig_username)
                use_flashapi = True

        if not use_flashapi:
            manager = IGSessionManager(burner, db)
            medias = manager.fetch_recent_posts(source.ig_username, amount=amount)
            burner.requests_today = (burner.requests_today or 0) + 1

            # Warmup: 15% chance to like 1 already-fetched post
            if medias and random.random() < 0.15:
                try:
                    post_to_like = random.choice(medias[:5])
                    manager.client.media_like(post_to_like.id)
                    burner.requests_today += 1
                    logger.debug("Warmup like on @%s", source.ig_username)
                except Exception:
                    pass

            db.commit()
            return medias

    # ── FlashAPI path ──────────────────────────────────────────────────────
    api_key = _get_flashapi_key(db)
    if not api_key:
        logger.warning(
            "FlashAPI backend requested for @%s but no API key is configured — skipping",
            source.ig_username,
        )
        return []

    from app.services.flashapi_client import FlashAPIClient
    import requests as _requests
    client = FlashAPIClient(api_key=api_key, base_url=settings.flashapi_base_url)
    try:
        return client.fetch_recent_posts(source.ig_username, amount=amount)
    except _requests.HTTPError as exc:
        status = exc.response.status_code if exc.response else 0
        if status in (400, 401, 403, 422):
            # Auth / bad-request errors — no point retrying, skip this source
            logger.warning(
                "FlashAPI %s for @%s — check API key or plan limits, skipping",
                status, source.ig_username,
            )
            return []
        raise  # 5xx or network errors: let the task retry normally
    except _requests.RequestException as exc:
        logger.warning("FlashAPI network error for @%s: %s — skipping", source.ig_username, exc)
        return []


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
