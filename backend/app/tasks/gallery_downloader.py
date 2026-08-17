"""Gallery downloader tasks — keep the image gallery stocked per keyword.

Beat ticks every 30 minutes; each active keyword self-throttles to one
download run per 24 hours (spec: daily), so "Download Now" from the UI can
still run any keyword immediately via download_keyword.delay().
"""

import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

from sqlalchemy.exc import IntegrityError

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

_KEYWORD_INTERVAL_HOURS = 24

# web/fetch is a paid call (jina-reader) — don't spend it where it won't pay
# off. "Where it pays off" splits into two tiers (2026-08-17, replacing one
# flat max_images cap that every keyword shared regardless of popularity):
#
# - Actively newsworthy (mentioned in a recently scraped article — a rider
#   racing every week, say) → genuinely new editorial coverage keeps showing
#   up on Getty every event, so it's worth building a deep archive: grows all
#   the way to _ACTIVE_KEYWORD_CEILING. This is gated on being IN THE NEWS,
#   not on how often OUR system has historically picked its photos — pick
#   rate is a lagging signal a keyword that was previously stuck at capacity
#   (see download_keyword's docstring) could never climb out of on its own.
# - Quiet (not currently in the news) → capped much lower
#   (_QUIET_KEYWORD_CEILING), and only topped up while its own cooldown-fresh
#   pool is thin relative to its actual pick rate (_fresh_supply_status) —
#   no point re-fetching a subject nothing is currently drawing from.
_MENTION_LOOKBACK_DAYS = 7
_ACTIVE_KEYWORD_CEILING = 500
_QUIET_KEYWORD_CEILING = 60

# Usage-based fresh-supply target for a QUIET keyword: tracks its actual pick
# rate instead of a number every keyword shares. FRESH_POOL_TARGET_WEEKS is
# how many weeks of fresh supply, at the observed pick rate, to stay ahead
# by; FLOOR is the minimum buffer even at ~0 usage, so a brand-new or
# never-mentioned keyword still bootstraps past zero instead of starving.
_USAGE_LOOKBACK_DAYS = 30
_FRESH_POOL_TARGET_WEEKS = 3
_FRESH_POOL_FLOOR = 8

logger = logging.getLogger(__name__)
settings = get_settings()


def _fresh_supply_status(db, keyword: str) -> tuple[int, int, int]:
    """(fresh_count, target, active_count) for one keyword.

    fresh_count: images currently past the reuse-cooldown gate (usable right
    now without violating [[gallery-reuse-cooldown]]/find_gallery_datauri's
    14-28 day window — using the floor here is intentionally conservative,
    i.e. this may slightly undercount what's "fresh").
    target: how big that fresh pool should be for a QUIET keyword, scaled to
    its actual pick rate over the last _USAGE_LOOKBACK_DAYS (a
    distinct-images-with-a-recent-last_used_at count — the closest proxy to
    "times picked" available without a dedicated usage-events log, and a
    reasonable one since the cooldown means an image is rarely picked twice
    inside one lookback window). Not used for an actively-newsworthy keyword
    — see _ACTIVE_KEYWORD_CEILING in download_all_keywords."""
    from app.models.gallery import GalleryImage
    from app.services.design_images import GALLERY_REUSE_COOLDOWN_MIN_DAYS

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cooldown_cutoff = now - timedelta(days=GALLERY_REUSE_COOLDOWN_MIN_DAYS)
    usage_cutoff = now - timedelta(days=_USAGE_LOOKBACK_DAYS)

    active_count = (
        db.query(GalleryImage)
        .filter(GalleryImage.keyword == keyword, GalleryImage.is_deleted == False)
        .count()
    )
    fresh_count = (
        db.query(GalleryImage)
        .filter(
            GalleryImage.keyword == keyword, GalleryImage.is_deleted == False,
            (GalleryImage.last_used_at.is_(None)) | (GalleryImage.last_used_at < cooldown_cutoff),
        )
        .count()
    )
    recent_picks = (
        db.query(GalleryImage)
        .filter(
            GalleryImage.keyword == keyword, GalleryImage.is_deleted == False,
            GalleryImage.last_used_at >= usage_cutoff,
        )
        .count()
    )
    weekly_rate = recent_picks / (_USAGE_LOOKBACK_DAYS / 7)
    target = int(min(_QUIET_KEYWORD_CEILING, max(_FRESH_POOL_FLOOR, round(weekly_rate * _FRESH_POOL_TARGET_WEEKS))))
    return fresh_count, target, active_count


def _recently_mentioned(db, keyword: str, cutoff: datetime) -> bool:
    """Whether `keyword` appears in any article scraped since `cutoff` —
    a proxy for "is this subject currently newsworthy", so gallery spend
    tracks what's actually being written about instead of blindly refreshing
    every keyword on a timer."""
    from sqlalchemy import or_
    from app.models.scraped_articles import ScrapedArticle

    needle = f"%{keyword.lower()}%"
    return (
        db.query(ScrapedArticle.id)
        .filter(
            ScrapedArticle.scraped_at >= cutoff,
            or_(
                ScrapedArticle.scraped_title.ilike(needle),
                ScrapedArticle.scraped_content.ilike(needle),
            ),
        )
        .first()
        is not None
    )


@celery_app.task(name="app.tasks.gallery_downloader.download_all_keywords")
def download_all_keywords():
    """Dispatch a download for every active keyword whose daily interval
    elapsed and that's below its tier's ceiling — actively-newsworthy
    keywords (mentioned in a recently scraped article) grow toward
    _ACTIVE_KEYWORD_CEILING regardless of past pick rate; quiet ones stop
    once their own cooldown-fresh pool already covers their actual usage
    (_fresh_supply_status), capped at the much smaller
    _QUIET_KEYWORD_CEILING. Both exist purely to avoid spending paid
    web/fetch calls where they won't help.

    Skips entirely while Settings.gallery_scraping_paused is set — a global
    kill switch for the scheduled sweep. Doesn't affect an explicit
    "Download Now" (download_keyword.delay called directly from the API) —
    that always runs regardless of these budget checks."""
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword
        from app.models.settings import Settings

        row = db.query(Settings).filter_by(id=1).first()
        if row and row.gallery_scraping_paused:
            logger.info("Gallery: scraping sweep paused globally — skipping")
            return

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        mention_cutoff = now - timedelta(days=_MENTION_LOOKBACK_DAYS)
        keywords = db.query(GalleryKeyword).filter(GalleryKeyword.is_active == True).all()

        for kw in keywords:
            if kw.last_downloaded_at and now - kw.last_downloaded_at < timedelta(hours=_KEYWORD_INTERVAL_HOURS):
                continue

            fresh_count, usage_target, active_count = _fresh_supply_status(db, kw.keyword)
            mentioned = _recently_mentioned(db, kw.keyword, mention_cutoff)

            if mentioned:
                if active_count >= _ACTIVE_KEYWORD_CEILING:
                    logger.debug(
                        "Gallery: keyword %r (active in the news) already at its ceiling (%d/%d) — skipping",
                        kw.keyword, active_count, _ACTIVE_KEYWORD_CEILING,
                    )
                    continue
                needed = _ACTIVE_KEYWORD_CEILING - active_count
            else:
                if active_count >= _QUIET_KEYWORD_CEILING:
                    logger.debug("Gallery: keyword %r (quiet) at its ceiling (%d/%d) — skipping", kw.keyword, active_count, _QUIET_KEYWORD_CEILING)
                    continue
                if fresh_count >= usage_target:
                    logger.debug(
                        "Gallery: keyword %r (quiet) has enough fresh supply for its usage (%d fresh >= target %d) — skipping",
                        kw.keyword, fresh_count, usage_target,
                    )
                    continue
                needed = min(usage_target - fresh_count, _QUIET_KEYWORD_CEILING - active_count)

            download_keyword.delay(kw.id, max_num=needed)
            logger.info(
                "Gallery: dispatched keyword %d (%s, %s) — active=%d, fresh=%d/target=%d, requesting %d",
                kw.id, kw.keyword, "newsworthy" if mentioned else "quiet", active_count, fresh_count, usage_target, needed,
            )
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.download_keyword", bind=True, max_retries=1)
def download_keyword(self, keyword_id: int, max_num: int | None = None):
    """Download new images for one keyword: collect URLs → dedup → validate → store.

    `max_num` caps how many NEW images this run may add. Defaults to the
    keyword's remaining room under its hard ceiling (max_images minus what's
    already active) rather than blindly requesting max_images every run —
    that bug was why 54/57 active keywords had silently grown 2x+ past their
    own cap (2026-08-17) and then got stuck skipped forever by the capacity
    check in download_all_keywords, which never revisits a count that only
    grows. The scheduled sweep now passes a smaller, usage-based figure via
    this param (see _fresh_supply_status); an explicit "Download Now" from
    the UI leaves it unset and gets the default top-up-to-ceiling behavior.
    """
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword, GalleryImage
        from app.services.image_downloader import download_images, keyword_slug

        kw = db.query(GalleryKeyword).filter_by(id=keyword_id).first()
        if not kw or not kw.is_active:
            return

        # Stamp immediately so a failing keyword still respects its interval
        kw.last_downloaded_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()

        active_count = (
            db.query(GalleryImage)
            .filter(GalleryImage.keyword == kw.keyword, GalleryImage.is_deleted == False)
            .count()
        )
        effective_max = max_num if max_num is not None else max(0, kw.max_images - active_count)
        if effective_max <= 0:
            logger.debug(
                "Gallery: keyword %d (%s) has no room to download (active=%d, cap=%d)",
                keyword_id, kw.keyword, active_count, kw.max_images,
            )
            return

        slug = keyword_slug(kw.keyword)
        dest_dir = Path(settings.storage_base_path) / "gallery" / slug

        # Dedup: all URLs ever stored for this keyword (global uniqueness is
        # still enforced by the unique constraint on insert)
        skip_urls = {
            url for (url,) in
            db.query(GalleryImage.source_image_url).filter(GalleryImage.keyword == kw.keyword).all()
        }

        try:
            results = download_images(
                keyword=kw.keyword,
                dest_dir=dest_dir,
                max_num=effective_max,
                min_size=(kw.min_width, kw.min_height),
                license_filter=kw.license_filter,
                skip_urls=skip_urls,
                max_pages=kw.max_pages,
            )
        except Exception as exc:
            kw.last_download_error = str(exc)[:512]
            db.commit()
            logger.error("Gallery: keyword %d (%s) download failed: %s", keyword_id, kw.keyword, exc)
            return

        saved = 0
        for item in results:
            image = GalleryImage(
                keyword=kw.keyword,
                source_image_url=item.source_url,
                local_path=item.local_path,
                public_url=f"{settings.storage_base_url.rstrip('/')}/gallery/{slug}/{item.filename}",
                width=item.width,
                height=item.height,
                source_engine=item.engine,
                license_info=kw.license_filter,
                label=item.label,
            )
            db.add(image)
            try:
                db.commit()
                saved += 1
            except IntegrityError:
                # URL already stored under another keyword — drop the duplicate file
                db.rollback()
                Path(item.local_path).unlink(missing_ok=True)

        kw.last_download_error = None
        db.commit()

        logger.info(
            "Gallery: keyword %d (%s) — %d candidates, %d saved",
            keyword_id, kw.keyword, len(results), saved,
        )

    except Exception as exc:
        db.rollback()
        logger.error("Gallery: keyword %d failed: %s", keyword_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=300)
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.scan_gallery_closeup_filter", bind=True, max_retries=1)
def scan_gallery_closeup_filter(self, criteria: str, keyword: str | None = None) -> dict:
    """Manual, on-demand AI scan (never automatic) — checks every non-deleted
    gallery image against an admin-typed criteria and reports which ones
    DON'T match. `keyword=None` scans the whole gallery; a specific keyword
    string scopes it to just that one. Read-only: nothing is ever deleted
    here. The API dispatches this and polls task state for progress
    ({"done", "total"} while running); the final return value is the
    candidate list the admin reviews and confirms via
    POST /gallery/images/bulk-delete — this task never deletes anything
    itself, by design, since a global scan can span the whole gallery."""
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryImage
        from app.services.design_images import classify_closeup_match

        q = db.query(GalleryImage).filter(GalleryImage.is_deleted == False)
        if keyword:
            q = q.filter(GalleryImage.keyword == keyword)
        images = q.all()
        total = len(images)

        candidates = []
        for i, image in enumerate(images):
            self.update_state(state="PROGRESS", meta={"done": i, "total": total})
            try:
                image_bytes = Path(image.local_path).read_bytes()
            except OSError as exc:
                logger.warning("Gallery: AI filter scan — image %d unreadable (%s), skipping", image.id, exc)
                continue

            result = classify_closeup_match(image_bytes, criteria)
            if not result["match"]:
                candidates.append({
                    "id": image.id,
                    "public_url": image.public_url,
                    "keyword": image.keyword,
                    "confidence": result["confidence"],
                })

        logger.info(
            "Gallery: AI filter scan (%s) — %d/%d flagged for review",
            keyword or "ALL KEYWORDS", len(candidates), total,
        )
        return {"done": total, "total": total, "candidates": candidates}

    except Exception as exc:
        logger.error("Gallery: AI filter scan failed (keyword=%s): %s", keyword, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()
