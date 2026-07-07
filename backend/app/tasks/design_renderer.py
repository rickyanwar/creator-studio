"""Design renderer tasks — turn pending_design news jobs into rendered PNGs.

Auto mode (spec Fitur 2 §D): pick an image (workflow §C cascade), send the
fanpage's Fabric.js template + AI title + image to the renderer service
(Puppeteer + Fabric.js, concurrency 1), store the PNG under
/var/www/media/designs/, and move the job to pending_publish.

Review-mode jobs are NOT auto-rendered — the admin opens them in the designer
UI, edits freely, and exports (the export endpoint stores the PNG the same
way). The beat sweep therefore only dispatches jobs whose fanpage is in auto
mode; render_design itself can also be triggered manually from the UI.
"""

import base64
import logging
import uuid
from pathlib import Path

import httpx

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_RENDER_TIMEOUT = 120.0


def select_image_for_job(db, job, fanpage, article) -> tuple[str | None, object | None]:
    """Workflow §C cascade. Returns (image_src_data_uri_or_none, gallery_image_or_none).

    1. gallery keywords that appear in the article title/content → unused image
    2. any unused image under the fanpage's gallery keywords
    3. article hero image (scraped_image_url), downloaded on the fly
    4. None → job needs a manual image
    """
    from app.models.gallery import GalleryImage

    keywords = [k.lower() for k in (fanpage.mode2_gallery_keywords or [])]
    text = f"{article.scraped_title} {article.scraped_content or ''}".lower()
    matched = [k for k in keywords if k in text]

    for pool in (matched, keywords):
        if not pool:
            continue
        img = (
            db.query(GalleryImage)
            .filter(GalleryImage.keyword.in_(pool), GalleryImage.is_used == False)
            .order_by(GalleryImage.downloaded_at.desc())
            .first()
        )
        if img:
            try:
                data = Path(img.local_path).read_bytes()
                return f"data:image/jpeg;base64,{base64.b64encode(data).decode()}", img
            except OSError as exc:
                logger.warning("Design: gallery image %d unreadable (%s) — skipping", img.id, exc)

    if article.scraped_image_url:
        try:
            resp = httpx.get(
                article.scraped_image_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)"},
                timeout=30.0, follow_redirects=True,
            )
            resp.raise_for_status()
            mime = resp.headers.get("content-type", "image/jpeg").split(";")[0]
            return f"data:{mime};base64,{base64.b64encode(resp.content).decode()}", None
        except Exception as exc:
            logger.warning("Design: hero image fetch failed for job %d: %s", job.id, exc)

    return None, None


@celery_app.task(name="app.tasks.design_renderer.render_design", bind=True, max_retries=2)
def render_design(self, job_id: int):
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage
        from app.models.scraped_articles import ScrapedArticle, ArticleStatus
        from app.models.design_templates import DesignTemplate

        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job or job.status != PublishJobStatus.pending_design or job.content_type != ContentType.news_content:
            return

        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        article = db.query(ScrapedArticle).filter_by(id=job.source_article_id).first()
        if not fanpage or not article:
            job.last_error = "fanpage or article missing"
            db.commit()
            return

        # ── Resolve template: job override → fanpage default → fanpage's default flag → shared default ──
        template = None
        if job.design_template_id:
            template = db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
        if not template and fanpage.mode2_default_template_id:
            template = db.query(DesignTemplate).filter_by(id=fanpage.mode2_default_template_id).first()
        if not template:
            template = (
                db.query(DesignTemplate)
                .filter(
                    DesignTemplate.is_default == True,
                    (DesignTemplate.fanpage_id == fanpage.id) | (DesignTemplate.fanpage_id.is_(None)),
                )
                .order_by(DesignTemplate.fanpage_id.desc().nullslast())
                .first()
            )
        if not template or not template.template_json:
            job.last_error = "no design template configured — create one in Template Designer"
            db.commit()
            logger.warning("Design: job %d has no usable template", job_id)
            return

        # ── Image selection (workflow §C) ──
        image_src, gallery_image = select_image_for_job(db, job, fanpage, article)
        if not image_src:
            job.last_error = "needs manual image — no gallery match and no article hero image"
            db.commit()
            logger.warning("Design: job %d needs a manual image", job_id)
            return

        # ── Render via Puppeteer + Fabric.js service ──
        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template.template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": job.design_title or article.scraped_title,
                "image_src": image_src,
            },
            timeout=_RENDER_TIMEOUT,
        )
        resp.raise_for_status()
        png_bytes = resp.content

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png_bytes)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.design_template_id = template.id
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        if gallery_image:
            gallery_image.is_used = True
        article.status = ArticleStatus.designed
        db.commit()

        logger.info("Design: job %d rendered → %s (%d bytes)", job_id, filename, len(png_bytes))

        # Auto-mode fanpages go straight to Repliz (Phase 2E); review-mode jobs
        # wait for the admin to publish from the queue/designer.
        from app.models.target_fanpages import PublishMode
        if fanpage.mode2_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("Design: job %d render failed: %s", job_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=180)
    finally:
        db.close()


@celery_app.task(name="app.tasks.design_renderer.render_pending_designs")
def render_pending_designs():
    """Sweep: auto-render pending_design jobs for fanpages in auto mode."""
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode

        jobs = (
            db.query(PublishJob.id)
            .join(TargetFanpage, TargetFanpage.id == PublishJob.fanpage_id)
            .filter(
                PublishJob.status == PublishJobStatus.pending_design,
                PublishJob.content_type == ContentType.news_content,
                TargetFanpage.mode2_publish_mode == PublishMode.auto,
            )
            .limit(10)
            .all()
        )
        for (job_id,) in jobs:
            render_design.delay(job_id)
        if jobs:
            logger.info("Design sweep: dispatched %d pending renders", len(jobs))
    finally:
        db.close()
