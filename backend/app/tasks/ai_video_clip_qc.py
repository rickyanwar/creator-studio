"""Celery task for per-clip Mode 8 logic QC."""

import logging
from datetime import timedelta

from sqlalchemy import or_

from app.database import SessionLocal
from app.tasks.celery_app import celery_app
from app.services.ai_video_dispatch import _utcnow
from app.services.ai_video_clip_qc import (
    CLIP_QC_WAIT_LIMIT_S, CLIP_QC_WAIT_RETRY_S,
    accept_attempt, best_attempt, logic_check, max_attempts_for_job,
)

logger = logging.getLogger(__name__)


def _wait(db, row, reason: str) -> None:
    row.status = "unavailable" if row.status == "pending" else row.status
    row.waiting_reason = reason
    row.processing_until = None
    if row.created_at and (_utcnow() - row.created_at).total_seconds() > CLIP_QC_WAIT_LIMIT_S:
        row.verdict = "failed"
        row.waiting_reason = f"clip QC unavailable for 6 h: {reason}"
    else:
        check_clip.apply_async((row.id,), countdown=CLIP_QC_WAIT_RETRY_S)
    db.commit()


def _still_current(db, job_id: int, generation: int) -> bool:
    from app.models.ai_video import VideoJob

    job = db.query(VideoJob).populate_existing().filter_by(id=job_id).first()
    return bool(job and job.status in ("claimed", "generating") and job.generation == generation)


@celery_app.task(name="app.tasks.ai_video_clip_qc.check_clip")
def check_clip(qc_id: int):
    from app.models.ai_video import VideoClipQc, VideoJob
    from app.models.target_fanpages import TargetFanpage
    from app.services.prompt_polish import PolishUnavailable, get_polish_config, repolish_clip
    from app.services.video_templates import fanpage_niche, get_template

    db = SessionLocal()
    try:
        row = db.query(VideoClipQc).filter_by(id=qc_id).first()
        if not row or row.verdict is not None:
            return
        now = _utcnow()
        claimed = db.query(VideoClipQc).filter(
            VideoClipQc.id == qc_id,
            VideoClipQc.verdict.is_(None),
            or_(VideoClipQc.processing_until.is_(None), VideoClipQc.processing_until < now),
        ).update({"processing_until": now + timedelta(minutes=40)}, synchronize_session=False)
        db.commit()
        if claimed == 0:
            return
        row = db.query(VideoClipQc).filter_by(id=qc_id).first()
        if not row or row.verdict is not None:
            return
        job = db.query(VideoJob).filter_by(id=row.video_job_id).first()
        if not job or job.status not in ("claimed", "generating") or job.generation != row.generation:
            return
        clip = next((item for item in (job.clips or []) if item.get("index") == row.clip_index), {})
        mode = clip.get("mode", "t2v")
        try:
            template = get_template(job.template_key)
            fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
            niche = fanpage_niche(fanpage)
        except Exception as exc:
            db.commit()
            _wait(db, row, f"clip QC setup error: {type(exc).__name__}")
            return
        previous = db.query(VideoClipQc).filter_by(video_job_id=job.id, generation=row.generation,
                                                   clip_index=row.clip_index - 1).all() if row.clip_index > 1 else []
        previous_row = next((item for item in previous if item.verdict == "pass"), None)
        if previous_row is None:
            previous_row = next((item for item in previous if item.verdict == "use_attempt" and item.use_attempt), None)
        previous_path = None
        if previous_row:
            if previous_row.verdict == "use_attempt":
                selected = next((item for item in previous if item.attempt == previous_row.use_attempt), None)
                previous_path = selected.clip_path if selected else None
            else:
                previous_path = previous_row.clip_path
        clip_path = row.clip_path
        clip_prompt = row.prompt
        clip_status = row.status
        clip_attempt = row.attempt
        clip_index = row.clip_index
        clips_total = len(job.clips or [])
        job_id = job.id
        max_attempts = max_attempts_for_job(job)
        generation = row.generation
        db.commit()

        if clip_status in ("pending", "unavailable"):
            try:
                issues = logic_check(clip_path, clip_prompt, previous_path)
            except Exception as exc:
                _wait(db, row, f"vision QC unavailable: {str(exc)[:200]}")
                return
            if not _still_current(db, job_id, generation):
                row.processing_until = None
                db.commit()
                return
            row.issues = issues
            row.major_count = sum(1 for issue in issues if issue.get("severity") == "major")
            row.minor_count = sum(1 for issue in issues if issue.get("severity") == "minor")
            row.status = "passed" if row.major_count == 0 else "failed"
            row.waiting_reason = None
            db.commit()

        clip_issues = row.issues or []

        if row.status == "passed":
            if not _still_current(db, job_id, generation):
                row.processing_until = None
                db.commit()
                return
            row.verdict = "pass"
            row.processing_until = None
            accept_attempt(db, job, row, accepted_best=False)
            db.commit()
            return

        if clip_attempt < max_attempts:
            config = get_polish_config(db)
            db.commit()
            next_prompt = clip_prompt
            if config is not None:
                try:
                    next_prompt = repolish_clip(clip_prompt, mode=mode, clip_index=clip_index,
                                                clips_total=clips_total, issues=clip_issues,
                                                template=template, niche=niche, config=config)
                except PolishUnavailable as exc:
                    _wait(db, row, f"re-polish unavailable: {exc.kind}")
                    return
                except ValueError:
                    logger.warning("Invalid clip re-polish answer for QC row %s", row.id)
                except Exception as exc:
                    _wait(db, row, f"re-polish unavailable: {type(exc).__name__}")
                    return
            if not _still_current(db, job_id, generation):
                row.processing_until = None
                db.commit()
                return
            row.verdict = "retry"
            row.next_prompt = next_prompt
            row.processing_until = None
            db.commit()
            return

        rows = db.query(VideoClipQc).filter_by(video_job_id=job_id, generation=generation,
                                               clip_index=clip_index).filter(VideoClipQc.issues.isnot(None)).all()
        selected = best_attempt(rows)
        if not _still_current(db, job_id, generation):
            row.processing_until = None
            db.commit()
            return
        row.verdict = "use_attempt"
        row.use_attempt = selected.attempt
        row.processing_until = None
        selected.processing_until = None
        accept_attempt(db, job, selected, accepted_best=True)
        db.commit()
    finally:
        db.close()
