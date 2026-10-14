"""Finalize uploaded Mode 8 videos through QC and the publish queue."""

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.database import SessionLocal
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _sibling_url(original_url: str | None, original_path: str | None, new_path: Path, base_url: str) -> str:
    """URL of a file written next to an already-published file (same folder, new name)."""
    if original_url and "/" in original_url:
        return f"{original_url.rsplit('/', 1)[0]}/{new_path.name}"
    return f"{base_url.rstrip('/')}/ai_videos/{new_path.name}"


@celery_app.task(name="app.tasks.ai_video_finalize.finalize_ai_video", queue="video", soft_time_limit=600)
def finalize_ai_video(video_job_id: int):
    from app.models.ai_video import VideoIdea, VideoJob
    from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
    from app.models.settings import Settings
    from app.models.target_fanpages import PublishMode, TargetFanpage
    from app.services.ai_video_qc import contact_sheet, technical_check, vision_check
    from app.config import get_settings

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
                    expectation_keys = (
                        "part", "mechanism_action", "final_state", "parts_list", "channel_name",
                        "moving_part", "fixed_parts",
                    )
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
        publish_video_path, publish_video_url = job.video_path, job.video_url
        publish_thumbnail_path, publish_thumbnail_url = job.thumbnail_path, job.thumbnail_url
        overlays_enabled = bool(fanpage and (fanpage.ai_video_logo_enabled or fanpage.ai_video_captions_enabled))
        overlay_info = {"logo": bool(fanpage and fanpage.ai_video_logo_enabled),
                        "captions": bool(fanpage and fanpage.ai_video_captions_enabled), "words": 0}
        if overlays_enabled:
            from app.services.ai_video_overlay import estimate_words, render_branded, speech_windows
            from app.services.fanpage_language import fanpage_language
            from app.services.speech_words import caption_words
            raw_path = Path(job.video_path)
            workdir = raw_path.parent / f"overlay_{job.id}_{uuid.uuid4().hex[:8]}"
            try:
                variables = idea.variables if idea and isinstance(idea.variables, dict) else {}
                texts = [variables.get(f"narration_{i}", "") for i in range(1, len(job.clips or []) + 1)]
                words = caption_words(str(raw_path), fanpage_language(fanpage) if fanpage else None, texts)
                overlay_info["timing"] = "voice" if words else "estimate"
                if not words:
                    windows = speech_windows(str(raw_path), job.clip_seconds, len(texts))
                    words = estimate_words(texts, windows)
                overlay_info["words"] = len(words)
                branded = raw_path.with_name(f"{raw_path.stem}_branded_{uuid.uuid4().hex[:8]}{raw_path.suffix}")
                render_branded(str(raw_path), str(branded), fanpage, words, workdir)
                thumbnail = branded.with_suffix(".jpg")
                import subprocess
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(branded), "-frames:v", "1", "-q:v", "3", str(thumbnail)], check=True, timeout=60)
                s = get_settings()
                publish_video_path = str(branded)
                publish_video_url = _sibling_url(job.video_url, job.video_path, branded, s.storage_base_url)
                publish_thumbnail_path = str(thumbnail)
                publish_thumbnail_url = _sibling_url(job.video_url, job.video_path, thumbnail, s.storage_base_url)
                raw_path.unlink(missing_ok=True)
                if job.thumbnail_path:
                    Path(job.thumbnail_path).unlink(missing_ok=True)
                job.video_path, job.video_url = publish_video_path, publish_video_url
                job.thumbnail_path, job.thumbnail_url = publish_thumbnail_path, publish_thumbnail_url
            except Exception as exc:
                publish_status = PublishJobStatus.pending_review
                overlay_info["error"] = str(exc)[:500]
                publish_video_path, publish_video_url = job.video_path, job.video_url
                publish_thumbnail_path, publish_thumbnail_url = job.thumbnail_path, job.thumbnail_url
                publish_job = PublishJob(
                    fanpage_id=job.fanpage_id, post_id=None, content_type=ContentType.ai_video,
                    design_title=job.title, ai_generated_caption=job.caption,
                    video_path=publish_video_path, video_url=publish_video_url,
                    video_thumbnail_path=publish_thumbnail_path, video_thumbnail_url=publish_thumbnail_url,
                    video_duration_s=job.duration_s, status=publish_status,
                    last_error=f"overlay failed: {exc}"[:2000],
                )
                db.add(publish_job)
                db.flush()
                job.qc = {"technical": technical, "vision": vision, "passed": False,
                          "reasons": reasons, "overlay": overlay_info}
                job.publish_job_id, job.status = publish_job.id, "done"
                if idea:
                    idea.status = "done"
                db.commit()
                return
        job.qc = {"technical": technical, "vision": vision, "passed": passed,
                  "reasons": reasons, "overlay": overlay_info}
        publish_job = PublishJob(
            fanpage_id=job.fanpage_id, post_id=None, content_type=ContentType.ai_video,
            design_title=job.title, ai_generated_caption=job.caption,
            video_path=publish_video_path, video_url=publish_video_url,
            video_thumbnail_path=publish_thumbnail_path, video_thumbnail_url=publish_thumbnail_url,
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
