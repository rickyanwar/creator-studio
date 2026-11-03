"""Check Flow audio on the video worker, which has the Whisper cache."""

from app.database import SessionLocal
from app.tasks.celery_app import celery_app

SPEECH_MIN_WORDS = 3


@celery_app.task(name="app.tasks.ai_video_speech_check.check_clip_speech")
def check_clip_speech(row_id: int) -> None:
    """Save attempt speech evidence before continuing clip QC."""
    from app.models.ai_video import VideoClipQc, VideoJob
    from app.models.target_fanpages import TargetFanpage
    from app.services.fanpage_language import fanpage_language
    from app.services.speech_words import transcribe_words
    from app.tasks.ai_video_clip_qc import check_clip

    db = SessionLocal()
    try:
        row = db.query(VideoClipQc).filter_by(id=row_id).first()
        if not row or row.verdict is not None:
            return
        job = db.query(VideoJob).filter_by(id=row.video_job_id).first()
        if not job or job.generation != row.generation or job.status not in ("claimed", "generating"):
            return
        fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
        path, language = row.clip_path, fanpage_language(fanpage)
        db.commit()
        try:
            words = transcribe_words(path, language)
            row.speech = ({"words": len(words), "text": " ".join(w.text for w in words)[:200]}
                          if words is not None else {"unavailable": True})
        except Exception:
            row.speech = {"unavailable": True}
        db.commit()
        check_clip.delay(row_id)
    finally:
        db.close()
