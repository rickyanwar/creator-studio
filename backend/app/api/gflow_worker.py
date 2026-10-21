"""GFlow worker API endpoints."""

import json
import logging
import re
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form
from pydantic import BaseModel, Field

from app.api.deps import DB, require_hermes_scope
from app.models.ai_video import VideoJob
from app.models.api_tokens import ApiToken
from app.services import ai_video_dispatch
from app.services.ai_video_dispatch import ConflictError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker/gflow", tags=["gflow_worker"])

WORKER_ID_REGEX = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _validate_worker_id(worker_id: str):
    if not WORKER_ID_REGEX.match(worker_id):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid worker_id format",
        )


class AccountHeartbeat(BaseModel):
    profile: str
    email: Optional[str] = None
    authenticated: Optional[bool] = False
    credits: Optional[int] = None
    error: Optional[str] = None
    error_class: Optional[str] = None


class HeartbeatRequest(BaseModel):
    worker_id: str
    version: Optional[str] = None
    max_concurrent: Optional[int] = None
    mem_available_mb: Optional[int] = None
    running_job_ids: List[int] = Field(default_factory=list)
    accounts: List[AccountHeartbeat] = Field(default_factory=list)


class ClaimRequest(BaseModel):
    worker_id: str


class ProgressRequest(BaseModel):
    worker_id: str
    generation: int
    event: str
    flow_project_id: Optional[str] = None
    clip_index: Optional[int] = None
    media_id: Optional[str] = None


class FailRequest(BaseModel):
    worker_id: str
    generation: int
    reason: str
    message: str


VALID_FAIL_REASONS = {"transient", "out_of_credits", "auth", "policy", "lost_checkpoint", "other", "upload_terms", "unusual_activity"}


@router.post("/heartbeat")
def heartbeat(
    req: HeartbeatRequest,
    db: DB,
    token: ApiToken = Depends(require_hermes_scope("gflow:worker")),
):
    _validate_worker_id(req.worker_id)
    return ai_video_dispatch.record_heartbeat(db, req.worker_id, req.model_dump())


@router.post("/claim")
def claim(
    req: ClaimRequest,
    db: DB,
    token: ApiToken = Depends(require_hermes_scope("gflow:worker")),
):
    _validate_worker_id(req.worker_id)
    claimed = ai_video_dispatch.claim_job(db, req.worker_id)
    if not claimed:
        # 204 No Content
        from fastapi import Response
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    job, acc = claimed
    return {
        "job": {
            "id": job.id,
            "generation": job.generation,
            "fanpage_id": job.fanpage_id,
            "template_key": job.template_key,
            "title": job.title,
            "model": job.model,
            "aspect": job.aspect,
            "clip_seconds": job.clip_seconds,
            "clips": job.clips,
            "clips_done": job.clips_done,
            "flow_project_id": job.flow_project_id,
            "project_name": f"ai-video-{job.id}-g{job.generation}",
            "cover_prompt": job.cover_prompt,
            "account": {
                "id": acc.id,
                "profile": acc.profile,
            },
            "lease_seconds": ai_video_dispatch.LEASE_MINUTES * 60,
        }
    }


@router.post("/jobs/{id}/progress")
def progress(
    id: int,
    req: ProgressRequest,
    db: DB,
    token: ApiToken = Depends(require_hermes_scope("gflow:worker")),
):
    _validate_worker_id(req.worker_id)
    job = db.query(VideoJob).filter(VideoJob.id == id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    try:
        ai_video_dispatch.record_progress(
            db,
            job,
            req.worker_id,
            req.generation,
            req.event,
            req.model_dump(),
        )
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"ok": True, "lease_seconds": ai_video_dispatch.LEASE_MINUTES * 60}


@router.post("/jobs/{id}/fail")
def fail(
    id: int,
    req: FailRequest,
    db: DB,
    token: ApiToken = Depends(require_hermes_scope("gflow:worker")),
):
    _validate_worker_id(req.worker_id)
    if req.reason not in VALID_FAIL_REASONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid failure reason '{req.reason}'",
        )

    job = db.query(VideoJob).filter(VideoJob.id == id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    try:
        ai_video_dispatch.record_failure(
            db,
            job,
            req.worker_id,
            req.generation,
            req.reason,
            req.message,
        )
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"status": job.status}


MAX_VIDEO_BYTES = 45 * 1024 * 1024  # 45 MB
MAX_THUMB_BYTES = 2 * 1024 * 1024   # 2 MB


@router.post("/jobs/{id}/complete")
def complete(
    id: int,
    db: DB,
    worker_id: str = Form(...),
    generation: int = Form(...),
    meta: str = Form(...),
    video: UploadFile = File(...),
    thumbnail: UploadFile = File(...),
    cover_bg: UploadFile | None = File(None),
    token: ApiToken = Depends(require_hermes_scope("gflow:worker")),
):
    _validate_worker_id(worker_id)
    job = db.query(VideoJob).filter(VideoJob.id == id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    try:
        meta_dict = json.loads(meta)
    except Exception:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid meta JSON")

    video_bytes = video.file.read()
    if len(video_bytes) > MAX_VIDEO_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Video too large")

    thumb_bytes = thumbnail.file.read()
    if len(thumb_bytes) > MAX_THUMB_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Thumbnail too large")

    cover_bg_bytes: bytes | None = None
    if cover_bg is not None:
        try:
            raw_cover = cover_bg.file.read()
            if len(raw_cover) > MAX_THUMB_BYTES * 2:
                logger.warning("cover_bg too large (%d bytes), ignoring", len(raw_cover))
            elif raw_cover:
                cover_bg_bytes = raw_cover
        except Exception as exc:
            logger.warning("Failed to read cover_bg: %s", exc)

    try:
        ai_video_dispatch.complete_job(
            db,
            job,
            worker_id,
            generation,
            video_bytes,
            thumb_bytes,
            meta_dict,
            cover_bg_content=cover_bg_bytes,
        )
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    return {"status": "uploaded"}
