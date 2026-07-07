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

logger = logging.getLogger(__name__)
settings = get_settings()


@celery_app.task(name="app.tasks.gallery_downloader.download_all_keywords")
def download_all_keywords():
    """Dispatch a download for every active keyword whose daily interval elapsed."""
    db = SessionLocal()
    try:
        from app.models.gallery import GalleryKeyword

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        keywords = db.query(GalleryKeyword).filter(GalleryKeyword.is_active == True).all()

        for kw in keywords:
            if kw.last_downloaded_at and now - kw.last_downloaded_at < timedelta(hours=_KEYWORD_INTERVAL_HOURS):
                continue
            download_keyword.delay(kw.id)
            logger.info("Gallery: dispatched keyword %d (%s)", kw.id, kw.keyword)
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
