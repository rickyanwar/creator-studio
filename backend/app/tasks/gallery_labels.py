import logging
import os
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import func
from app.database import SessionLocal
from app.models.gallery import GalleryImage
from app.models.settings import Settings
from app.services.gallery_labeler import label_image, LabelerError, LabelerUnavailable
from app.services.muse_labeler import merge_label_attributes
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _get_daily_budget(db, s: Settings) -> int:
    """Calculate remaining labeling budget for today (UTC)."""
    now_utc = datetime.now(timezone.utc)
    today_start = datetime(now_utc.year, now_utc.month, now_utc.day)

    labeled_today = (
        db.query(GalleryImage)
        .filter(
            GalleryImage.labeled_at >= today_start,
        )
        .count()
    )
    return max(0, s.gallery_labeling_daily_cap - labeled_today)


@celery_app.task(name="app.tasks.gallery_labels.label_gallery_image")
def label_gallery_image(image_id: int, force: bool = False, relabel: bool = False):
    """Label one gallery image by ID."""
    db = SessionLocal()
    try:
        s = db.query(Settings).filter_by(id=1).first()
        if not s or (not force and (not s.gallery_labeling_enabled or not s.gallery_labeling_since)):
            return

        budget = _get_daily_budget(db, s) if not force else 1
        if budget <= 0:
            logger.info("label_gallery_image: daily cap reached, skipping image %d", image_id)
            return

        img = db.query(GalleryImage).filter_by(id=image_id).first()
        if not img or img.is_deleted or (img.labeled_at is not None and not relabel):
            return
        if not img.downloaded_at or (not force and (not s.gallery_labeling_since or img.downloaded_at < s.gallery_labeling_since)):
            return
        if not os.path.exists(img.local_path):
            return

        try:
            attrs = img.attributes or {}
            hints = dict(attrs.get("owner_hints") or {})
            event = attrs.get("event") or {}
            if "session" in attrs.get("manual_fields", []) or "event.session" in attrs.get("manual_fields", []):
                hints["session"] = event.get("session")
            for key in ("event_name", "year"):
                if f"event.{key}" in attrs.get("manual_fields", []):
                    hints[key] = event.get(key)
            if "people" in attrs.get("manual_fields", []):
                hints["people"] = [person.get("name") for person in attrs.get("people", [])]
            norm = label_image(img.local_path, provider="router", keyword=img.keyword, hints=hints or None)
            img.attributes = merge_label_attributes(img.attributes, norm)
            img.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
            img.label_error = None
            db.commit()
            logger.info("label_gallery_image: successfully labeled image %d", image_id)
        except (ValueError, LabelerError) as exc:
            img.label_attempts = (img.label_attempts or 0) + 1
            img.label_error = str(exc)[:300]
            db.commit()
            logger.warning("label_gallery_image: error on image %d: %s", image_id, exc)
        except LabelerUnavailable as exc:
            logger.warning("label_gallery_image: labeler unavailable for image %d: %s", image_id, exc)

    finally:
        db.close()


@celery_app.task(name="app.tasks.gallery_labels.label_new_gallery_images")
def label_new_gallery_images() -> int:
    """Safety net task: picks up to min(budget, 40) unlabeled images and labels them."""
    db = SessionLocal()
    processed = 0
    try:
        s = db.query(Settings).filter_by(id=1).first()
        if not s or not s.gallery_labeling_enabled or not s.gallery_labeling_since:
            return 0

        budget = _get_daily_budget(db, s)
        if budget <= 0:
            logger.info("label_new_gallery_images: daily cap reached")
            return 0

        limit = min(budget, 40)
        safety_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
        candidates = (
            db.query(GalleryImage)
            .filter(
                GalleryImage.is_deleted == False,
                GalleryImage.labeled_at.is_(None),
                GalleryImage.downloaded_at >= s.gallery_labeling_since,
                GalleryImage.downloaded_at <= safety_cutoff,
                GalleryImage.label_attempts < 3,
            )
            .order_by(GalleryImage.downloaded_at.asc())
            .limit(limit)
            .all()
        )

        for img in candidates:
            if not os.path.exists(img.local_path):
                continue

            try:
                norm = label_image(img.local_path, provider="router", keyword=img.keyword)
                img.attributes = merge_label_attributes(img.attributes, norm)
                img.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
                img.label_error = None
                db.commit()
                processed += 1
                time.sleep(0.5)
            except (ValueError, LabelerError) as exc:
                img.label_attempts = (img.label_attempts or 0) + 1
                img.label_error = str(exc)[:300]
                db.commit()
                logger.warning("label_new_gallery_images: error on image %d: %s", img.id, exc)
                time.sleep(0.5)
            except LabelerUnavailable as exc:
                # Do not increment attempts, stop the tick
                logger.warning("label_new_gallery_images: labeler unavailable on image %d: %s, stopping tick", img.id, exc)
                break

        return processed
    finally:
        db.close()
