from typing import Optional
from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DB
from app.schemas.publish_job import PublishJobOut, PublishJobCaptionUpdate, RegenerateCaptionRequest

router = APIRouter(prefix="/publish-jobs", tags=["publish-jobs"])


def _enrich_job(job, db) -> PublishJobOut:
    from app.models.posts import Post
    from app.models.target_fanpages import TargetFanpage
    from app.models.ig_sources import IGSource

    post = db.query(Post).filter_by(id=job.post_id).first()
    fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
    ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first() if post else None

    out = PublishJobOut.model_validate(job)
    out.fanpage_name = fanpage.name if fanpage else None
    out.fanpage_picture_url = fanpage.picture_url if fanpage else None
    out.ig_username = ig_source.ig_username if ig_source else None
    out.image_public_urls = list(post.image_public_urls) if post and post.image_public_urls else []
    out.image_source_urls = list(post.image_source_urls) if post and post.image_source_urls else []
    out.media_type = post.media_type.value if post else None
    return out


@router.get("", response_model=list[PublishJobOut])
def list_jobs(
    db: DB,
    _: CurrentUser,
    status: Optional[str] = Query(None),
    fanpage_id: Optional[int] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0),
):
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    q = db.query(PublishJob)
    if status:
        try:
            q = q.filter(PublishJob.status == PublishJobStatus(status))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown status: {status}")
    if fanpage_id:
        q = q.filter(PublishJob.fanpage_id == fanpage_id)

    jobs = q.order_by(PublishJob.created_at.desc()).offset(offset).limit(limit).all()
    return [_enrich_job(j, db) for j in jobs]


@router.get("/{job_id}", response_model=PublishJobOut)
def get_job(job_id: int, db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _enrich_job(job, db)


@router.put("/{job_id}/caption", response_model=PublishJobOut)
def update_caption(job_id: int, body: PublishJobCaptionUpdate, db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.ai_generated_caption = body.caption
    db.commit()
    db.refresh(job)
    return _enrich_job(job, db)


@router.post("/{job_id}/regenerate-caption", response_model=PublishJobOut)
def regenerate_caption(job_id: int, body: RegenerateCaptionRequest, db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.tasks.ai_generator import generate_caption_for_job

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = PublishJobStatus.pending_caption
    db.commit()

    generate_caption_for_job.delay(job_id, force_provider=body.provider)
    db.refresh(job)
    return _enrich_job(job, db)


@router.post("/{job_id}/publish", response_model=PublishJobOut)
def manual_publish(job_id: int, db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.tasks.publisher import publish_job

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.status not in (PublishJobStatus.pending_review, PublishJobStatus.failed):
        raise HTTPException(status_code=400, detail=f"Cannot publish job in status: {job.status}")

    job.status = PublishJobStatus.pending_publish
    db.commit()

    publish_job.delay(job_id)
    db.refresh(job)
    return _enrich_job(job, db)


@router.post("/{job_id}/skip", response_model=PublishJobOut)
def skip_job(job_id: int, db: DB, _: CurrentUser):
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job.status = PublishJobStatus.skipped
    db.commit()
    db.refresh(job)
    return _enrich_job(job, db)
