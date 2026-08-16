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

# web/fetch is a paid call (jina-reader) — don't spend it on a keyword that's
# already at capacity, and only spend it on a subject that's currently
# newsworthy (mentioned in a recently scraped article) unless it's critically
# under-stocked, so a quiet keyword doesn't get stuck at 0 forever. See
# [[feedback-maximize-9router-fallbacks]]'s sibling lesson from 2026-08-16:
# cost-consciousness here, reliability there — different tradeoffs for a paid
# per-call API vs a retry chain.
_MENTION_LOOKBACK_DAYS = 7
_MENTION_BOOTSTRAP_FLOOR = 5

logger = logging.getLogger(__name__)
settings = get_settings()


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
    elapsed, an already-full keyword, and (unless critically low on stock) a
    keyword that hasn't been mentioned in a recently scraped article — both
    checks exist purely to avoid spending paid web/fetch calls where they
    won't help (full) or aren't currently relevant (not in the news).

    Skips entirely while Settings.gallery_scraping_paused is set — a global
    kill switch for the scheduled sweep. Doesn't affect an explicit
    "Download Now" (download_keyword.delay called directly from the API) —
    that always runs regardless of these budget checks."""
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword, GalleryImage
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

            active_count = (
                db.query(GalleryImage)
                .filter(GalleryImage.keyword == kw.keyword, GalleryImage.is_deleted == False)
                .count()
            )
            if active_count >= kw.max_images:
                logger.debug("Gallery: keyword %r already at capacity (%d/%d) — skipping", kw.keyword, active_count, kw.max_images)
                continue

            if active_count >= _MENTION_BOOTSTRAP_FLOOR and not _recently_mentioned(db, kw.keyword, mention_cutoff):
                logger.debug(
                    "Gallery: keyword %r not mentioned in any article in the last %d days — skipping this cycle",
                    kw.keyword, _MENTION_LOOKBACK_DAYS,
                )
                continue

            download_keyword.delay(kw.id)
            logger.info("Gallery: dispatched keyword %d (%s) — active=%d/%d", kw.id, kw.keyword, active_count, kw.max_images)
    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_downloader.download_keyword", bind=True, max_retries=1)
def download_keyword(self, keyword_id: int):
    """Download new images for one keyword: collect URLs → dedup → validate → store."""
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
                max_num=kw.max_images,
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
