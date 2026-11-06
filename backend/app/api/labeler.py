import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func

from app.api.deps import DB, require_hermes_scope
from app.models.gallery import GalleryImage
from app.services import muse_labeler

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/labeler", tags=["labeler"])


class ClaimRequest(BaseModel):
    limit: int = Field(ge=1, le=50)
    worker: str = Field(min_length=1, max_length=64)
    label: Optional[Literal["face", "action", "other"]] = None


class ResultsRequest(BaseModel):
    results: list[dict[str, Any]] = Field(max_length=50)
    worker: str | None = Field(None, min_length=1, max_length=64)


@router.get("/schema")
def schema(token=Depends(require_hermes_scope("labels:write"))):
    return muse_labeler.label_schema()


@router.post("/claim")
def claim(body: ClaimRequest, db: DB, token=Depends(require_hermes_scope("labels:write"))):
    items = muse_labeler.claim_images(db, body.limit, body.worker, label=body.label)
    logger.info("Muse label claim worker=%s count=%d", body.worker, len(items))
    return items


@router.post("/results")
def results(body: ResultsRequest, db: DB, token=Depends(require_hermes_scope("labels:write"))):
    worker = body.worker or "muse"
    response = muse_labeler.process_results(db, body.model_dump(), worker)
    logger.info("Muse label results worker=%s saved=%d failed=%d skipped=%d", worker, response["saved"], response["failed"], len(response["skipped"]))
    return response


@router.get("/stats")
def stats(db: DB, token=Depends(require_hermes_scope("labels:write"))):
    active = db.query(GalleryImage).filter(GalleryImage.is_deleted.is_(False))
    labeled = active.filter(GalleryImage.labeled_at.is_not(None)).count()
    muse_labeled = active.filter(GalleryImage.attributes["source"].astext == "muse").count()
    failed = active.filter(GalleryImage.label_attempts >= 3).count()
    pending = active.filter(GalleryImage.label_pending.is_(True), GalleryImage.label_attempts < 3)
    remaining = pending.count()
    leased = 0
    client = muse_labeler.redis_client()
    for row in pending.with_entities(GalleryImage.id):
        if client.get(f"{muse_labeler.LEASE_PREFIX}{row.id}"):
            leased += 1
    hour_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    labeled_last_hour = active.filter(GalleryImage.labeled_at >= hour_ago).count()
    total = active.count()
    week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
    recent = db.query(GalleryImage).filter(GalleryImage.downloaded_at >= week_ago)
    muse_rejected = recent.filter(GalleryImage.is_deleted.is_(True), GalleryImage.label_error.like("muse gate:%")).count()
    app_rejected = recent.filter(GalleryImage.is_deleted.is_(True), GalleryImage.label_error == "app gate: not usable").count()
    muse_kept = recent.filter(GalleryImage.is_deleted.is_(False), GalleryImage.label_pending.is_(False), GalleryImage.attributes["source"].astext == "muse").count()
    muse_total = muse_rejected + muse_kept
    return {"total": total, "labeled": labeled, "labeled_by_muse": muse_labeled,
            "remaining": remaining, "failed": failed, "leased": leased,
            "labeled_last_hour": labeled_last_hour, "pending": remaining,
            "muse_rejected_7d": muse_rejected, "app_fallback_rejected_7d": app_rejected,
            "muse_kept_7d": muse_kept,
            "muse_reject_rate_7d": round(muse_rejected / muse_total, 2) if muse_total else None}
