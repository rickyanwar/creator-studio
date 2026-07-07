from typing import Optional
from fastapi import APIRouter, File, HTTPException, Query, UploadFile

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

    if job.status not in (PublishJobStatus.pending_review, PublishJobStatus.pending_publish, PublishJobStatus.failed):
        raise HTTPException(status_code=400, detail=f"Cannot publish job in status: {job.status}")

    job.status = PublishJobStatus.pending_publish
    db.commit()

    publish_job.delay(job_id)
    db.refresh(job)
    return _enrich_job(job, db)


@router.get("/{job_id}/design-payload")
def get_design_payload(job_id: int, db: DB, _: CurrentUser):
    """Everything the designer page needs to preload a news job."""
    from app.models.publish_jobs import PublishJob, ContentType
    from app.models.target_fanpages import TargetFanpage
    from app.models.scraped_articles import ScrapedArticle
    from app.models.design_templates import DesignTemplate
    from app.models.gallery import GalleryImage

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job or job.content_type != ContentType.news_content:
        raise HTTPException(status_code=404, detail="News job not found")

    fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
    article = db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()

    # Same resolution cascade as the auto-renderer (design_renderer.render_design):
    # job override → fanpage default → fanpage/shared template flagged is_default
    template = None
    template_id = job.design_template_id or (fanpage.mode2_default_template_id if fanpage else None)
    if template_id:
        template = db.query(DesignTemplate).filter_by(id=template_id).first()
    if not template and fanpage:
        template = (
            db.query(DesignTemplate)
            .filter(
                DesignTemplate.is_default == True,
                (DesignTemplate.fanpage_id == fanpage.id) | (DesignTemplate.fanpage_id.is_(None)),
            )
            .order_by(DesignTemplate.fanpage_id.desc().nullslast())
            .first()
        )

    # image candidates: fanpage gallery keywords first, then the article hero
    candidates: list[dict] = []
    if fanpage and fanpage.mode2_gallery_keywords:
        imgs = (
            db.query(GalleryImage)
            .filter(GalleryImage.keyword.in_([k.lower() for k in fanpage.mode2_gallery_keywords]))
            .order_by(GalleryImage.is_used.asc(), GalleryImage.downloaded_at.desc())
            .limit(24)
            .all()
        )
        candidates = [
            {"public_url": i.public_url, "keyword": i.keyword, "is_used": i.is_used, "width": i.width, "height": i.height}
            for i in imgs
        ]
    if article and article.scraped_image_url:
        candidates.insert(0, {"public_url": article.scraped_image_url, "keyword": "article hero", "is_used": False, "width": None, "height": None})

    return {
        "job_id": job.id,
        "status": job.status.value,
        "design_title": job.design_title,
        "caption": job.ai_generated_caption,
        "design_image_url": job.design_image_url,
        "article_title": article.scraped_title if article else None,
        "article_url": article.article_url if article else None,
        "fanpage_id": job.fanpage_id,
        "fanpage_name": fanpage.name if fanpage else None,
        "template": {
            "id": template.id,
            "name": template.name,
            "canvas_width": template.canvas_width,
            "canvas_height": template.canvas_height,
            "template_json": template.template_json,
        } if template else None,
        "image_candidates": candidates,
    }


@router.post("/{job_id}/design-image", response_model=PublishJobOut)
async def upload_design_image(job_id: int, db: DB, _: CurrentUser, file: UploadFile = File(...)):
    """Store an exported design PNG (from the review-mode designer) and move
    the job to pending_publish."""
    import uuid as _uuid
    from pathlib import Path
    from app.config import get_settings
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
    from app.models.scraped_articles import ScrapedArticle, ArticleStatus

    settings = get_settings()
    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job or job.content_type != ContentType.news_content:
        raise HTTPException(status_code=404, detail="News job not found")

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    designs_dir = Path(settings.storage_base_path) / "designs"
    designs_dir.mkdir(parents=True, exist_ok=True)
    filename = f"job_{job.id}_{_uuid.uuid4().hex[:8]}.png"
    (designs_dir / filename).write_bytes(data)

    job.design_image_path = str(designs_dir / filename)
    job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
    job.status = PublishJobStatus.pending_publish
    job.last_error = None

    article = db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()
    if article:
        article.status = ArticleStatus.designed

    db.commit()

    # Exporting from the designer is the review-mode approval → publish now.
    from app.tasks.publisher import publish_job
    publish_job.delay(job.id)

    db.refresh(job)
    return _enrich_job(job, db)


@router.post("/{job_id}/render-now", response_model=PublishJobOut)
def render_now(job_id: int, db: DB, _: CurrentUser):
    """Manually trigger the headless auto-render for a pending_design job."""
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
    from app.tasks.design_renderer import render_design

    job = db.query(PublishJob).filter_by(id=job_id).first()
    if not job or job.content_type != ContentType.news_content:
        raise HTTPException(status_code=404, detail="News job not found")
    if job.status != PublishJobStatus.pending_design:
        raise HTTPException(status_code=400, detail=f"Cannot render job in status: {job.status.value}")

    render_design.delay(job_id)
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
