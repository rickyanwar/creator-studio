import logging
import os
import time
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import func, cast, String
from app.database import SessionLocal
from app.models.gallery import GalleryImage
from app.models.settings import Settings
from app.services.gallery_labeler import label_image, LabelerError, LabelerUnavailable
from app.services.muse_labeler import merge_label_attributes
from app.services.gallery_label_budget import reserve, release
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_WIB = ZoneInfo("Asia/Jakarta")


def _get_now() -> datetime:
    return datetime.now(timezone.utc)


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

        img = db.query(GalleryImage).filter_by(id=image_id).first()
        if not img or img.is_deleted or (img.labeled_at is not None and not relabel):
            return
        if not img.downloaded_at or (not force and (not s.gallery_labeling_since or img.downloaded_at < s.gallery_labeling_since)):
            return
        if not os.path.exists(img.local_path):
            return

        if not force:
            if not reserve(s.gallery_labeling_daily_cap, db=db):
                logger.info("label_gallery_image: daily cap reached, skipping image %d", image_id)
                return

        reserved = not force
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
            if reserved:
                release()
            logger.warning("label_gallery_image: labeler unavailable for image %d: %s", image_id, exc)
        except Exception:
            if reserved:
                release()
            raise

    finally:
        db.close()


def _is_relabel_candidate(img: GalleryImage) -> bool:
    attrs = img.attributes or {}
    if "keyword_person_visible" in attrs:
        return False
    if "people" in (attrs.get("manual_fields") or []):
        return False
    people = attrs.get("people") or []
    return any(isinstance(p, dict) and p.get("source") == "keyword" for p in people)


def _pick_label_candidates(
    db, s: Settings, limit: int, safety_cutoff: datetime
) -> list[tuple[GalleryImage, str]]:
    """Pick up to limit images in priority order: new, relabel, backfill."""
    candidates: list[tuple[GalleryImage, str]] = []

    # a. NEW (unchanged)
    new_rows = (
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
    for img in new_rows:
        candidates.append((img, "new"))

    # b. RELABEL
    if len(candidates) < limit:
        needed = limit - len(candidates)
        relabel_batch = (
            db.query(GalleryImage)
            .filter(
                GalleryImage.is_deleted == False,
                GalleryImage.labeled_at.isnot(None),
                GalleryImage.label_attempts < 3,
                cast(GalleryImage.attributes["people"], String).like('%"keyword"%'),
                cast(GalleryImage.attributes, String).notlike("%keyword_person_visible%"),
            )
            .order_by(GalleryImage.labeled_at.asc())
            .limit(limit * 20)
            .all()
        )
        for img in relabel_batch:
            if _is_relabel_candidate(img):
                candidates.append((img, "relabel"))
                if len(candidates) >= limit:
                    break

    # c. BACKFILL
    if len(candidates) < limit:
        needed = limit - len(candidates)
        backfill_rows = (
            db.query(GalleryImage)
            .filter(
                GalleryImage.is_deleted == False,
                GalleryImage.labeled_at.is_(None),
                GalleryImage.downloaded_at < s.gallery_labeling_since,
                GalleryImage.label_attempts < 3,
            )
            .order_by(GalleryImage.downloaded_at.desc())
            .limit(needed)
            .all()
        )
        for img in backfill_rows:
            attrs = img.attributes or {}
            if "people" in (attrs.get("manual_fields") or []):
                continue
            candidates.append((img, "backfill"))

    return candidates


@celery_app.task(name="app.tasks.gallery_labels.label_new_gallery_images")
def label_new_gallery_images(db=None, *, now: datetime | None = None) -> int:
    """Safety net task: runs sweep at night (00:00-06:00 WIB) up to min(cap, 40).
    Outside night hours, only processes images downloaded in the last 6 hours
    with labeled_at IS NULL (cap 10 per run)."""
    close_db = db is None
    db = db or SessionLocal()
    processed = 0
    try:
        s = db.query(Settings).filter_by(id=1).first()
        if not s or not s.gallery_labeling_enabled or not s.gallery_labeling_since:
            return 0

        curr_time = now if now is not None else _get_now()
        if curr_time.tzinfo is None:
            now_utc = curr_time.replace(tzinfo=timezone.utc)
            now_wib = now_utc.astimezone(_WIB)
        else:
            now_wib = curr_time.astimezone(_WIB)
            now_utc = curr_time.astimezone(timezone.utc)

        is_night = 0 <= now_wib.hour < 6
        now_naive = now_utc.replace(tzinfo=None)
        six_hours_ago = now_naive - timedelta(hours=6)

        if not is_night:
            limit = min(s.gallery_labeling_daily_cap, 10)
            recent_rows = (
                db.query(GalleryImage)
                .filter(
                    GalleryImage.is_deleted == False,
                    GalleryImage.labeled_at.is_(None),
                    GalleryImage.downloaded_at >= six_hours_ago,
                    GalleryImage.label_attempts < 3,
                )
                .order_by(GalleryImage.downloaded_at.asc())
                .limit(limit)
                .all()
            )
            if not recent_rows:
                logger.info(
                    "label_new_gallery_images: daytime outside 00:00-06:00 WIB (hour=%d) "
                    "and no recent unlabeled images in last 6h — skipping sweep",
                    now_wib.hour,
                )
                return 0
            candidates = [(img, "new") for img in recent_rows]
        else:
            limit = min(s.gallery_labeling_daily_cap, 40)
            safety_cutoff = now_naive - timedelta(minutes=15)
            candidates = _pick_label_candidates(db, s, limit, safety_cutoff)

        counts = {"new": 0, "relabel": 0, "backfill": 0}

        for img, kind in candidates:
            if not os.path.exists(img.local_path):
                img.label_attempts = 3
                img.label_error = "file missing"
                db.commit()
                continue

            if not reserve(s.gallery_labeling_daily_cap, db=db):
                logger.info("label_new_gallery_images: daily cap reached")
                break

            # End the transaction before the slow vision call; an idle-in-transaction
            # connection blocks every migration ALTER (incident 2026-10-24).
            local_path, keyword = img.local_path, img.keyword
            db.commit()

            try:
                norm = label_image(local_path, provider="router", keyword=keyword)
                img.attributes = merge_label_attributes(img.attributes, norm)
                img.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
                img.label_error = None
                db.commit()
                processed += 1
                counts[kind] += 1
                time.sleep(0.5)
            except (ValueError, LabelerError) as exc:
                img.label_attempts = (img.label_attempts or 0) + 1
                img.label_error = str(exc)[:300]
                db.commit()
                logger.warning("label_new_gallery_images: error on image %d: %s", img.id, exc)
                time.sleep(0.5)
            except LabelerUnavailable as exc:
                # Do not increment attempts, release slot, stop the tick
                release()
                logger.warning("label_new_gallery_images: labeler unavailable on image %d: %s, stopping tick", img.id, exc)
                break
            except Exception:
                release()
                raise

        logger.info(
            "label_new_gallery_images: new=%d relabel=%d backfill=%d",
            counts["new"],
            counts["relabel"],
            counts["backfill"],
        )
        return processed
    finally:
        if close_db:
            db.close()
