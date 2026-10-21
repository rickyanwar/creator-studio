from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal
from app.models.vision_check_cache import VisionCheckCache

logger = logging.getLogger(__name__)

CACHE_DAYS = 90


def image_digest(image_bytes: bytes) -> str:
    return hashlib.sha256(image_bytes).hexdigest()


def get_cached(kind: str, digest: str, key: str) -> dict | None:
    try:
        db = SessionLocal()
        try:
            row = (
                db.query(VisionCheckCache)
                .filter(
                    VisionCheckCache.kind == kind,
                    VisionCheckCache.image_sha256 == digest,
                    VisionCheckCache.key == key,
                )
                .first()
            )
            if not row:
                return None
            created = row.created_at
            if created:
                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=CACHE_DAYS)
                if created.tzinfo is not None:
                    created = created.astimezone(timezone.utc).replace(tzinfo=None)
                if created < cutoff:
                    return None
            logger.info("vision cache hit: %s", kind)
            return row.result
        finally:
            db.close()
    except Exception as exc:
        logger.warning("vision_cache get_cached failed: %s", exc)
        return None


def put_cached(kind: str, digest: str, key: str, result: dict) -> None:
    try:
        db = SessionLocal()
        try:
            row = (
                db.query(VisionCheckCache)
                .filter(
                    VisionCheckCache.kind == kind,
                    VisionCheckCache.image_sha256 == digest,
                    VisionCheckCache.key == key,
                )
                .first()
            )
            if row:
                row.result = result
                row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                row = VisionCheckCache(
                    kind=kind,
                    image_sha256=digest,
                    key=key,
                    result=result,
                    created_at=datetime.now(timezone.utc).replace(tzinfo=None),
                )
                db.add(row)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    except Exception as exc:
        logger.warning("vision_cache put_cached failed: %s", exc)
