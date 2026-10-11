"""Finalize uploaded Mode 8 videos through QC and the publish queue."""

import logging
from datetime import datetime, timezone

from app.database import SessionLocal
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.ai_video_finalize.finalize_ai_video", queue="video", soft_time_limit=600)
def finalize_ai_video(video_job_id: int):
    from app.models.ai_video import VideoIdea, VideoJob
    from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
    from app.models.settings import Settings
    from app.models.target_fanpages import PublishMode, TargetFanpage
    from app.services.ai_video_qc import contact_sheet, technical_check, vision_check

    db = SessionLocal()
    try:
        job = db.query(VideoJob).filter_by(id=video_job_id).first()
        if not job or job.status != "uploaded":
            return
        idea = db.query(VideoIdea).filter_by(id=job.idea_id).first() if job.idea_id else None
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        technical = technical_check(job.video_path, len(job.clips), job.clip_seconds, job.aspect)
        vision = None
        reasons = list(technical["reasons"])
        settings = db.query(Settings).filter_by(id=1).first()
        if technical["passed"] and settings and settings.ai_video_vision_qc_enabled:
            try:
                expectations = None
                if idea and isinstance(idea.variables, dict):
                    expectation_keys = ("part", "mechanism_action", "final_state", "parts_list", "channel_name")
                    expectations = {
                        key: idea.variables[key]
                        for key in expectation_keys
                        if key in idea.variables and idea.variables[key] is not None
                    }
                    if not expectations:
                        expectations = None
                vision = vision_check(contact_sheet(job.video_path), job.title, expectations)
            except Exception:
                vision = {
                    "passed": False,
                    "issues": ["vision QC unavailable: contact sheet failed"],
                    "raw": None,
                }
            reasons.extend(vision["issues"])
        if not technical["passed"]:
            job.qc = {"technical": technical, "vision": vision, "passed": False, "reasons": reasons}
            job.status, job.error = "failed", "; ".join(reasons)[:2000]
            if idea:
                idea.status, idea.error = "failed", job.error
            db.commit()
            return

        passed = vision is None or vision["passed"]
        job.qc = {"technical": technical, "vision": vision, "passed": passed, "reasons": reasons}
        publish_status = PublishJobStatus.pending_publish if vision is None or vision["passed"] else PublishJobStatus.pending_review
        publish_job = PublishJob(
            fanpage_id=job.fanpage_id, post_id=None, content_type=ContentType.ai_video,
            design_title=job.title, ai_generated_caption=job.caption,
            video_path=job.video_path, video_url=job.video_url,
            video_thumbnail_path=job.thumbnail_path, video_thumbnail_url=job.thumbnail_url,
            video_duration_s=job.duration_s, status=publish_status,
            last_error=None if publish_status == PublishJobStatus.pending_publish else "QC: " + "; ".join(reasons),
        )
        db.add(publish_job)
        db.flush()
        job.publish_job_id, job.status, job.finished_at = publish_job.id, "done", datetime.now(timezone.utc).replace(tzinfo=None)
        if idea:
            idea.status = "done"
        db.commit()
        if publish_status == PublishJobStatus.pending_publish and fanpage.ai_video_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job as publish_task
            publish_task.delay(publish_job.id)
    except Exception as exc:
        db.rollback()
        failed_job = db.query(VideoJob).filter_by(id=video_job_id).first()
        if failed_job:
            failed_job.status = "failed"
            failed_job.error = f"finalize error: {type(exc).__name__}"
            failed_idea = db.query(VideoIdea).filter_by(id=failed_job.idea_id).first() if failed_job.idea_id else None
            if failed_idea:
                failed_idea.status = "failed"
                failed_idea.error = failed_job.error
            db.commit()
        logger.exception("AI video finalization failed for job %s", video_job_id)
    finally:
        db.close()
