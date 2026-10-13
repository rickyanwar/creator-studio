"""Admin API endpoints for AI video jobs and accounts."""

from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import FileResponse
from pathlib import Path
import tempfile
from pydantic import BaseModel, Field

from app.api.deps import DB, CurrentUser, get_current_user
from app.models.ai_video import VideoJob, GflowAccount, GflowWorker
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_dispatch
from app.services.ai_video_dispatch import ConflictError
from app.schemas.fanpage import FanpageUpdate

router = APIRouter(prefix="/ai-video", tags=["ai_video_admin"])


class AccountUpdate(BaseModel):
    enabled: Optional[bool] = None
    label: Optional[str] = Field(None, max_length=100)
    terms_accepted: Optional[bool] = None


class OverlayPreviewRequest(BaseModel):
    ai_video_logo_enabled: bool = True
    ai_video_logo_size_pct: int = Field(14, ge=5, le=30)
    ai_video_logo_position: str = Field("top_right", pattern=r"^(top_right|top_left|bottom_right|bottom_left)$")
    ai_video_logo_margin_pct: int = Field(3, ge=0, le=10)
    ai_video_captions_enabled: bool = True
    ai_video_caption_size: int = Field(76, ge=40, le=140)
    ai_video_caption_bottom_pct: int = Field(29, ge=5, le=70)
    ai_video_caption_highlight: str = Field("#FFE500", pattern=r"^#[0-9A-Fa-f]{6}$")


_preview_bearer = HTTPBearer()


def _preview_auth(credentials: HTTPAuthorizationCredentials = Depends(_preview_bearer)):
    return get_current_user(credentials)


@router.post("/fanpages/{fanpage_id}/overlay-preview")
def overlay_preview(fanpage_id: int, body: OverlayPreviewRequest, db: DB, user: str = Depends(_preview_auth)):
    from app.models.ai_video import VideoJob
    from app.services.ai_video_overlay import preview_frame
    fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fanpage:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    job = db.query(VideoJob).filter_by(fanpage_id=fanpage_id, status="done").order_by(VideoJob.finished_at.desc()).first()
    source = job.video_path if job and job.video_path and Path(job.video_path).exists() else None
    workdir = Path(tempfile.mkdtemp(prefix="ai-video-preview-"))
    settings = body.model_dump()
    from app.services.design_images import watermark_datauri
    uri = watermark_datauri(fanpage) if body.ai_video_logo_enabled else None
    if uri:
        logo = workdir / "logo.png"
        import base64
        logo.write_bytes(base64.b64decode(uri.split(",", 1)[1]))
        settings["watermark_image_path"] = str(logo)
    settings["watermark_text"] = fanpage.watermark_text
    out = workdir / "preview.png"
    preview_frame(settings, source, out)
    return FileResponse(out, media_type="image/png", filename="ai-video-overlay-preview.png")


def _serialize_job(job: VideoJob, fanpage_name: Optional[str], account: Optional[GflowAccount]) -> dict:
    clips_total = len(job.clips) if job.clips else 0
    clips_done_count = len(job.clips_done) if job.clips_done else 0
    account_dict = None
    if account:
        account_dict = {
            "id": account.id,
            "profile": account.profile,
            "email": account.email,
        }

    return {
        "id": job.id,
        "fanpage_id": job.fanpage_id,
        "fanpage_name": fanpage_name or "",
        "idea_id": job.idea_id,
        "template_key": job.template_key,
        "title": job.title,
        "status": job.status,
        "clips_total": clips_total,
        "clips_done": clips_done_count,
        "credits_estimate": job.credits_estimate,
        "account": account_dict,
        "worker_id": job.worker_id,
        "generation": job.generation,
        "restarts": job.restarts,
        "error": job.error,
        "qc": job.qc,
        "video_url": job.video_url,
        "thumbnail_url": job.thumbnail_url,
        "publish_job_id": job.publish_job_id,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@router.get("/jobs")
def list_jobs(
    db: DB,
    user: CurrentUser,
    status: Optional[str] = None,
    fanpage_id: Optional[int] = None,
    limit: int = Query(50, ge=1, le=200),
):
    query = db.query(VideoJob)
    if status:
        query = query.filter(VideoJob.status == status)
    if fanpage_id:
        query = query.filter(VideoJob.fanpage_id == fanpage_id)

    jobs = query.order_by(VideoJob.created_at.desc()).limit(limit).all()

    # Pre-fetch fanpage names and accounts
    fp_ids = list({j.fanpage_id for j in jobs})
    acc_ids = list({j.account_id for j in jobs if j.account_id})

    fanpages = {fp.id: fp.name for fp in db.query(TargetFanpage).filter(TargetFanpage.id.in_(fp_ids)).all()} if fp_ids else {}
    accounts = {acc.id: acc for acc in db.query(GflowAccount).filter(GflowAccount.id.in_(acc_ids)).all()} if acc_ids else {}

    return [_serialize_job(j, fanpages.get(j.fanpage_id), accounts.get(j.account_id)) for j in jobs]


@router.post("/jobs/{id}/retry")
def retry_job_endpoint(id: int, db: DB, user: CurrentUser):
    job = db.query(VideoJob).filter(VideoJob.id == id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    try:
        ai_video_dispatch.retry_job(db, job)
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    fp = db.query(TargetFanpage).filter(TargetFanpage.id == job.fanpage_id).first()
    acc = db.query(GflowAccount).filter(GflowAccount.id == job.account_id).first() if job.account_id else None
    return _serialize_job(job, fp.name if fp else None, acc)


@router.post("/jobs/{id}/cancel")
def cancel_job_endpoint(id: int, db: DB, user: CurrentUser):
    job = db.query(VideoJob).filter(VideoJob.id == id).first()
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")

    try:
        ai_video_dispatch.cancel_job(db, job)
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e))

    fp = db.query(TargetFanpage).filter(TargetFanpage.id == job.fanpage_id).first()
    acc = db.query(GflowAccount).filter(GflowAccount.id == job.account_id).first() if job.account_id else None
    return _serialize_job(job, fp.name if fp else None, acc)


@router.get("/accounts")
def list_accounts(db: DB, user: CurrentUser):
    accounts = db.query(GflowAccount).order_by(GflowAccount.id.asc()).all()

    # Find busy job per account
    busy_jobs = (
        db.query(VideoJob.account_id, VideoJob.id)
        .filter(
            VideoJob.account_id.isnot(None),
            VideoJob.status.in_(("claimed", "generating")),
        )
        .all()
    )
    busy_map = {acc_id: job_id for acc_id, job_id in busy_jobs}

    result = []
    for acc in accounts:
        result.append({
            "id": acc.id,
            "worker_id": acc.worker_id,
            "profile": acc.profile,
            "email": acc.email,
            "label": acc.label,
            "enabled": acc.enabled,
            "authenticated": acc.authenticated,
            "status": acc.status,
            "credits": acc.credits,
            "credits_checked_at": acc.credits_checked_at,
            "last_error": acc.last_error,
            "last_seen_at": acc.last_seen_at,
            "last_used_at": acc.last_used_at,
            "busy_job_id": busy_map.get(acc.id),
        })
    return result


@router.patch("/accounts/{id}")
def update_account(id: int, req: AccountUpdate, db: DB, user: CurrentUser):
    account = db.query(GflowAccount).filter(GflowAccount.id == id).first()
    if not account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if req.enabled is not None:
        account.enabled = req.enabled
    if req.label is not None:
        account.label = req.label
    if req.terms_accepted and account.status == "needs_terms":
        account.status = "ok"
        account.last_error = None

    db.commit()
    db.refresh(account)

    busy_job = (
        db.query(VideoJob.id)
        .filter(
            VideoJob.account_id == account.id,
            VideoJob.status.in_(("claimed", "generating")),
        )
        .first()
    )

    return {
        "id": account.id,
        "worker_id": account.worker_id,
        "profile": account.profile,
        "email": account.email,
        "label": account.label,
        "enabled": account.enabled,
        "authenticated": account.authenticated,
        "status": account.status,
        "credits": account.credits,
        "credits_checked_at": account.credits_checked_at,
        "last_error": account.last_error,
        "last_seen_at": account.last_seen_at,
        "last_used_at": account.last_used_at,
        "busy_job_id": busy_job.id if busy_job else None,
    }


@router.get("/workers")
def list_workers(db: DB, user: CurrentUser):
    workers = db.query(GflowWorker).all()
    now = ai_video_dispatch._utcnow()
    threshold = now - ai_video_dispatch.timedelta(seconds=ai_video_dispatch.WORKER_ONLINE_SECONDS)

    result = []
    for w in workers:
        online = bool(w.last_seen_at and w.last_seen_at >= threshold)
        result.append({
            "worker_id": w.worker_id,
            "last_seen_at": w.last_seen_at,
            "online": online,
            "version": w.version,
            "max_concurrent": w.max_concurrent,
            "mem_available_mb": w.mem_available_mb,
            "running_job_ids": w.running_job_ids or [],
        })
    return result
