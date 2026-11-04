"""Rewrite prompts after a motorcycle video's first policy refusal."""

from datetime import datetime, timezone

from app.database import SessionLocal
from app.models.ai_video import VideoIdea, VideoJob
from app.services.ai_video_brand_safe import rewrite_brand_safe
from app.tasks.celery_app import celery_app


@celery_app.task(name="app.tasks.ai_video_brand_safe.rewrite_job_brand_safe")
def rewrite_job_brand_safe(job_id):
    db = SessionLocal()
    try:
        job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
        if not job or job.status != "queued" or not (job.error or "").startswith("policy: brand-safe retry pending"):
            return
        new = rewrite_brand_safe(job.clips)
        if new:
            job.clips = new
            job.not_before = None
            job.error = "policy: retrying once with brand names removed"
        else:
            job.status = "failed"
            job.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            if job.idea_id:
                idea = db.query(VideoIdea).filter(VideoIdea.id == job.idea_id).first()
                if idea:
                    idea.status = "failed"
                    idea.error = job.error
        db.commit()
    finally:
        db.close()
