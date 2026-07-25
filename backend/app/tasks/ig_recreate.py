"""Mode 3 — recreate a scraped Instagram post as a designed image.

For a fanpage with ig_recreate_enabled, classify the post image via 9Router
vision (quote / news / other):
  - quote → put the extracted quote on the fanpage's QUOTE template
  - news  → AI-rewrite the extracted headline, put it on the NEWS template
  - other → skip (no job)
Both use the IG post image as the photo. The design job renders via the same
headless renderer, then publishes like a news design job.
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


def _rewrite_news_title(text: str, fanpage) -> str:
    from app.services.ai_caption import generate_caption

    prompt = (
        f'Rewrite this news headline for the Facebook page "{fanpage.name}".\n'
        f'HEADLINE: "{text}"\n'
        f"- Language: {fanpage.caption_language}\n"
        f"- Keep all facts and names; make it more engaging (stronger hook).\n"
        f"- Max {fanpage.mode2_title_max_chars or 80} characters.\n"
        f"Output ONLY the rewritten headline — no quotes, no explanation."
    )
    title, _ = generate_caption(prompt)
    return title.strip().strip('"')


@celery_app.task(name="app.tasks.ig_recreate.recreate_post_for_fanpage", bind=True, max_retries=3)
def recreate_post_for_fanpage(self, post_id: int, fanpage_id: int):
    db = SessionLocal()
    try:
        from app.models.posts import Post
        from app.models.target_fanpages import TargetFanpage
        from app.models.ig_sources import IGSource
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType, AIProvider
        from app.services.ai_caption import build_caption_prompt, generate_caption, GroqRateLimitError
        from app.services.ig_content_classifier import classify_ig_image

        # Idempotency: one job per (post, fanpage)
        if db.query(PublishJob).filter_by(post_id=post_id, fanpage_id=fanpage_id).first():
            return

        post = db.query(Post).filter_by(id=post_id).first()
        fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
        if not post or not fanpage or not fanpage.ig_recreate_enabled:
            return
        if not post.image_local_paths:
            logger.warning("IG-recreate: post %d has no images", post_id)
            return

        ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first()
        niche = (ig_source.ig_username if ig_source else None) or fanpage.name

        with open(post.image_local_paths[0], "rb") as f:
            image_bytes = f.read()
        try:
            cls = classify_ig_image(image_bytes, niche=niche)
        except Exception as exc:
            logger.warning("IG-recreate: classify failed post %d fp %d: %s", post_id, fanpage_id, exc)
            raise self.retry(exc=exc, countdown=180)

        ctype = cls["type"]
        if ctype == "other" or not cls["text"]:
            logger.info("IG-recreate: post %d fp %d classified '%s' → skipped", post_id, fanpage_id, ctype)
            return

        if ctype == "quote":
            template_id = fanpage.ig_recreate_quote_template_id
            design_title = cls["text"]
        else:  # news
            template_id = fanpage.ig_recreate_news_template_id
            try:
                design_title = _rewrite_news_title(cls["text"], fanpage)
            except GroqRateLimitError:
                raise self.retry(countdown=120)

        if not template_id:
            logger.warning("IG-recreate: fanpage %d has no %s template configured", fanpage_id, ctype)
            return

        # FB caption (per-source caption criteria override the fanpage's Mode-1)
        try:
            caption, provider = generate_caption(
                build_caption_prompt(fanpage, niche, cls["text"], source=ig_source)
            )
        except GroqRateLimitError:
            raise self.retry(countdown=120)

        job = PublishJob(
            post_id=post_id,
            fanpage_id=fanpage_id,
            content_type=ContentType.ig_recreate,
            design_title=design_title,
            ai_generated_caption=caption,
            ai_provider_used=AIProvider(provider),
            design_template_id=template_id,
            status=PublishJobStatus.pending_design,
        )
        db.add(job)
        db.commit()
        logger.info("IG-recreate: post %d fp %d → %s job %d", post_id, fanpage_id, ctype, job.id)
        render_ig_recreate.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("IG-recreate error post %d fp %d: %s", post_id, fanpage_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=120)
    finally:
        db.close()


@celery_app.task(name="app.tasks.ig_recreate.render_ig_recreate", bind=True, max_retries=2)
def render_ig_recreate(self, job_id: int):
    db = SessionLocal()
    try:
        from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
        from app.models.target_fanpages import TargetFanpage, PublishMode
        from app.models.posts import Post
        from app.models.design_templates import DesignTemplate

        job = db.query(PublishJob).filter_by(id=job_id).first()
        if not job or job.status != PublishJobStatus.pending_design or job.content_type != ContentType.ig_recreate:
            return

        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        post = db.query(Post).filter_by(id=job.post_id).first()
        template = (
            db.query(DesignTemplate).filter_by(id=job.design_template_id).first()
            if job.design_template_id else None
        )
        if not fanpage or not post or not template or not template.template_json:
            job.last_error = "missing fanpage/post/template for IG-recreate"
            db.commit()
            return
        if not post.image_local_paths:
            job.last_error = "post has no image"
            db.commit()
            return

        # Default photo = the IG post image (fallback only).
        main_path = post.image_local_paths[0]
        with open(main_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()
        image_src = f"data:image/jpeg;base64,{b64}"

        # Two-slot templates also get a secondary photo: circular inset → the
        # second subject's portrait placed opposite the main face; rect slot →
        # a two-subject split with style-consistent photos.
        from app.services.design_images import (
            prepare_design_images, extract_two_subjects, focus_points_for, source_news_main,
        )
        from app.models.ig_sources import IGSource
        ig_source = db.query(IGSource).filter_by(id=post.ig_source_id).first()
        niche = (ig_source.ig_username if ig_source else None) or fanpage.name

        # News recreate: use a clean, relevant photo (gallery → fresh search) by the
        # headline's subject instead of the IG screenshot. Falls back to the IG
        # image if nothing is found.
        if job.design_template_id == fanpage.ig_recreate_news_template_id:
            src, path = source_news_main(db, job.design_title or "", niche)
            if src:
                image_src, main_path = src, path

        smart = bool(fanpage.ig_recreate_smart_layout)
        # Smart layout: a news headline about TWO people → use the split template
        if (smart and fanpage.ig_recreate_split_template_id
                and job.design_template_id == fanpage.ig_recreate_news_template_id):
            primary, secondary = extract_two_subjects(job.design_title or "", niche)
            if primary and secondary:
                split_tpl = db.query(DesignTemplate).filter_by(id=fanpage.ig_recreate_split_template_id).first()
                if split_tpl and split_tpl.template_json:
                    template = split_tpl
                    logger.info("IG-recreate: smart split for job %d (%s | %s)", job.id, primary, secondary)

        template_json, image_srcs = prepare_design_images(
            db, template.template_json, template.canvas_width,
            job.design_title or "", niche, image_src, main_path=main_path, smart=smart,
            expand=bool(fanpage.design_expand),
        )

        resp = httpx.post(
            f"{settings.renderer_url.rstrip('/')}/render",
            json={
                "template_json": template_json,
                "width": template.canvas_width,
                "height": template.canvas_height,
                "title": job.design_title,
                "image_srcs": image_srcs,
                "focus_points": focus_points_for(image_srcs),
                "scale": settings.design_render_scale,
            },
            timeout=_RENDER_TIMEOUT,
        )
        resp.raise_for_status()
        png = resp.content

        designs_dir = Path(settings.storage_base_path) / "designs"
        designs_dir.mkdir(parents=True, exist_ok=True)
        filename = f"job_{job.id}_{uuid.uuid4().hex[:8]}.png"
        (designs_dir / filename).write_bytes(png)

        job.design_image_path = str(designs_dir / filename)
        job.design_image_url = f"{settings.storage_base_url.rstrip('/')}/designs/{filename}"
        job.status = PublishJobStatus.pending_publish
        job.last_error = None
        db.commit()
        logger.info("IG-recreate: job %d rendered → %s (%d bytes)", job_id, filename, len(png))

        if fanpage.publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)

    except Exception as exc:
        from celery.exceptions import Retry, MaxRetriesExceededError
        if isinstance(exc, (Retry, MaxRetriesExceededError)):
            raise
        db.rollback()
        logger.error("IG-recreate render job %d failed: %s", job_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=180)
    finally:
        db.close()
