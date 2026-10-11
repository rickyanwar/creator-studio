import logging
from datetime import datetime, timedelta, timezone
from typing import Any

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


class ResultsRequest(BaseModel):
    results: list[dict[str, Any]] = Field(max_length=50)


@router.get("/schema")
def schema(token=Depends(require_hermes_scope("labels:write"))):
    return muse_labeler.label_schema()


@router.post("/claim")
def claim(body: ClaimRequest, db: DB, token=Depends(require_hermes_scope("labels:write"))):
    items = muse_labeler.claim_images(db, body.limit, body.worker)
    logger.info("Muse label claim worker=%s count=%d", body.worker, len(items))
    return items


@router.post("/results")
def results(body: ResultsRequest, db: DB, token=Depends(require_hermes_scope("labels:write"))):
    response = muse_labeler.process_results(db, body.model_dump(), "muse")
    logger.info("Muse label results saved=%d failed=%d skipped=%d", response["saved"], response["failed"], len(response["skipped"]))
    return response


@router.get("/stats")
def stats(db: DB, token=Depends(require_hermes_scope("labels:write"))):
    active = db.query(GalleryImage).filter(GalleryImage.is_deleted.is_(False))
    labeled = active.filter(GalleryImage.attributes.is_not(None)).count()
    muse_labeled = active.filter(GalleryImage.attributes["source"].astext == "muse").count()
    failed = active.filter(GalleryImage.label_attempts >= 3).count()
    leased = 0
    client = muse_labeler.redis_client()
    for row in active.filter(GalleryImage.attributes.is_(None), GalleryImage.label_attempts < 3).with_entities(GalleryImage.id):
        if client.get(f"{muse_labeler.LEASE_PREFIX}{row.id}"):
            leased += 1
    hour_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1)
    labeled_last_hour = active.filter(GalleryImage.labeled_at >= hour_ago).count()
    total = active.count()
    return {"total": total, "labeled": labeled, "labeled_by_muse": muse_labeled,
            "remaining": total - labeled - failed, "failed": failed, "leased": leased,
            "labeled_last_hour": labeled_last_hour}
