"""Finalize uploaded Mode 8 videos through QC and the publish queue."""

import logging
import shutil
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


def _apply_tts_narration(job, idea, reasons) -> str:
    """Narrate a TTS-mode video. Returns "ok", "review" (narrated, but a speech check asks for review) or
    "failed" (no narration; the original video is kept)."""
    from app.config import get_settings
    from app.services.ai_video_tts import TtsError, narrate_video
    from app.services.fanpage_language import fanpage_language
    from app.services.speech_words import transcribe_words
    from app.tasks.ai_video_speech_check import SPEECH_MIN_WORDS
    from app.models.target_fanpages import TargetFanpage
    from sqlalchemy.orm import object_session

    workdir = None
    try:
        speech_review = False
        if not any(isinstance(clip.get("qc"), dict) and clip["qc"].get("attempt") for clip in job.clips or []):
            try:
                db = object_session(job)
                fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
                words = transcribe_words(job.video_path, fanpage_language(fanpage))
            except Exception:
                words = None
            if words is None:
                reasons.append("speech check unavailable")
                speech_review = True
            elif len(words) >= SPEECH_MIN_WORDS:
                reasons.append("human speech found in the Flow audio")
                speech_review = True
        raw = Path(job.video_path)
        workdir = raw.parent / f"tts_{job.id}_{uuid.uuid4().hex[:8]}"
        variables = idea.variables if idea and isinstance(idea.variables, dict) else {}
        texts = [variables.get(f"narration_{i}", "") for i in range(1, len(job.clips or []) + 1)]
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise TtsError("missing narration sentence")
        result = narrate_video(job, texts, str(workdir))
        url = _sibling_url(job.video_url, job.video_path, Path(result["video_path"]), get_settings().storage_base_url)
        narration = {**job.narration, "model": result["model"], "segments": result["segments"],
                     "audio_path": result["audio_path"]}
        raw.unlink(missing_ok=True)
        job.video_path, job.video_url = result["video_path"], url
        job.narration = narration
        return "review" if speech_review else "ok"
    except Exception as exc:
        reasons.append(f"tts narration failed: {exc}"[:300])
        job.narration = {**job.narration, "error": str(exc)[:500]}
        return "failed"
    finally:
        if workdir is not None:
            shutil.rmtree(workdir, ignore_errors=True)


def _apply_asmr(job, idea, reasons) -> str:
    """Normalize the speech-free bed and save the on-screen narration windows."""
    from app.config import get_settings
    from app.models.target_fanpages import TargetFanpage
    from app.services.ai_video_tts import LEAD_IN_S, TAIL_GAP_S, clip_windows, normalize_bed
    from app.services.fanpage_language import fanpage_language
    from app.services.speech_words import transcribe_words
    from app.tasks.ai_video_speech_check import SPEECH_MIN_WORDS
    from sqlalchemy.orm import object_session

    try:
        speech_review = False
        if not any(isinstance(clip.get("qc"), dict) and clip["qc"].get("attempt") for clip in job.clips or []):
            try:
                db = object_session(job)
                fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
                words = transcribe_words(job.video_path, fanpage_language(fanpage))
            except Exception:
                words = None
            if words is None:
                reasons.append("speech check unavailable")
                speech_review = True
            elif len(words) >= SPEECH_MIN_WORDS:
                reasons.append("human speech found in the Flow audio")
                speech_review = True
        variables = idea.variables if idea and isinstance(idea.variables, dict) else {}
        texts = [variables.get(f"narration_{i}", "") for i in range(1, len(job.clips or []) + 1)]
        if not texts or any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError("missing narration sentence")
        windows = clip_windows(job.video_path, len(job.clips), job.clip_seconds or 10)
        segments = [{"index": i, "text": text, "start": s + LEAD_IN_S,
                     "duration": (e - s) - LEAD_IN_S - TAIL_GAP_S}
                    for i, (text, (s, e)) in enumerate(zip(texts, windows), start=1)]
        if len(segments) != len(texts) or any(segment["duration"] <= 0 for segment in segments):
            raise ValueError("invalid ASMR caption windows")
        raw = Path(job.video_path)
        normalized = raw.with_name(f"{raw.stem}_asmr_{uuid.uuid4().hex[:8]}.mp4")
        normalize_bed(str(raw), str(normalized))
        url = _sibling_url(job.video_url, job.video_path, normalized, get_settings().storage_base_url)
        raw.unlink(missing_ok=True)
        job.video_path, job.video_url = str(normalized), url
        job.narration = {**job.narration, "segments": segments}
        return "review" if speech_review else "ok"
    except Exception as exc:
        reasons.append(f"asmr normalization failed: {exc}"[:300])
        job.narration = {**job.narration, "error": str(exc)[:500]}
        return "failed"


class _CaptionsEnabled:
    """Use the page's caption style without changing its saved caption switch."""

    ai_video_captions_enabled = True

    def __init__(self, fanpage):
        self._fanpage = fanpage

    def __getattr__(self, name):
        return getattr(self._fanpage, name)


def _asmr_caption_words(job):
    from app.services.ai_video_overlay import estimate_words

    segments = job.narration.get("segments") or []
    return estimate_words([segment["text"] for segment in segments],
                          [(s["start"], s["start"] + s["duration"]) for s in segments])


def _tts_caption_words(job, language_code):
    """Time final segment text against the clean narrator, or its fitted windows."""
    from app.services.ai_video_overlay import estimate_words
    from app.services.speech_words import caption_words

    narration = job.narration
    segments = narration.get("segments") or []
    texts = [segment["text"] for segment in segments]
    audio = narration.get("audio_path")
    words = caption_words(audio, language_code, texts) if audio and Path(audio).is_file() else None
    if words is None:
        words = estimate_words(texts, [(s["start"], s["start"] + s["duration"]) for s in segments])
    return words


@celery_app.task(name="app.tasks.ai_video_finalize.finalize_ai_video", queue="video", soft_time_limit=600)
def finalize_ai_video(video_job_id: int):
    from app.models.ai_video import VideoIdea, VideoJob
    from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
    from app.models.settings import Settings
    from app.models.target_fanpages import PublishMode, TargetFanpage
    from app.services.ai_video_qc import contact_sheet, technical_check, vision_check
    from app.services.qa_toggles import qa_enabled
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
        if technical["passed"] and settings and settings.ai_video_vision_qc_enabled and qa_enabled(fanpage, "ai_video"):
            try:
                expectations = None
                if idea and isinstance(idea.variables, dict):
                    expectation_keys = (
                        "part", "mechanism_action", "final_state", "parts_list", "channel_name",
                        "moving_part", "fixed_parts", "visual_subject",
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
        from app.services.ai_video_clip_qc import max_attempts_for_job
        for clip in job.clips or []:
            qc = clip.get("qc") or {}
            if qc.get("accepted_best"):
                publish_status = PublishJobStatus.pending_review
                issues = qc.get("issues") or []
                first_problem = issues[0].get("problem") if issues and isinstance(issues[0], dict) else "logic issues"
                attempts = max_attempts_for_job(job)
                reasons.append(f"clip {clip['index']}: kept the best of {attempts} attempts ({first_problem})")
        tts_failed = False
        is_asmr = (job.narration or {}).get("engine") == "asmr"
        if (job.narration or {}).get("engine") == "tts":
            tts_result = _apply_tts_narration(job, idea, reasons)
            if tts_result != "ok":
                publish_status = PublishJobStatus.pending_review
                passed = False
            tts_failed = tts_result == "failed"
        elif is_asmr:
            asmr_result = _apply_asmr(job, idea, reasons)
            if asmr_result != "ok":
                publish_status = PublishJobStatus.pending_review
                passed = False
            tts_failed = asmr_result == "failed"
        publish_video_path, publish_video_url = job.video_path, job.video_url
        publish_thumbnail_path, publish_thumbnail_url = job.thumbnail_path, job.thumbnail_url
        overlays_enabled = is_asmr or bool(fanpage and (fanpage.ai_video_logo_enabled or fanpage.ai_video_captions_enabled))
        overlay_info = {"logo": bool(fanpage and fanpage.ai_video_logo_enabled),
                         "captions": is_asmr or bool(fanpage and fanpage.ai_video_captions_enabled), "words": 0}
        if overlays_enabled and not tts_failed:
            from app.services.ai_video_overlay import estimate_words, render_branded, speech_windows
            from app.services.fanpage_language import fanpage_language
            from app.services.speech_words import caption_words
            raw_path = Path(job.video_path)
            workdir = raw_path.parent / f"overlay_{job.id}_{uuid.uuid4().hex[:8]}"
            try:
                if is_asmr:
                    words = _asmr_caption_words(job)
                    overlay_info["timing"] = "asmr"
                elif (job.narration or {}).get("engine") == "tts":
                    words = _tts_caption_words(job, fanpage_language(fanpage) if fanpage else None)
                    overlay_info["timing"] = "tts"
                else:
                    variables = idea.variables if idea and isinstance(idea.variables, dict) else {}
                    texts = [variables.get(f"narration_{i}", "") for i in range(1, len(job.clips or []) + 1)]
                    words = caption_words(str(raw_path), fanpage_language(fanpage) if fanpage else None, texts)
                    overlay_info["timing"] = "voice" if words else "estimate"
                    if not words:
                        windows = speech_windows(str(raw_path), job.clip_seconds, len(texts))
                        words = estimate_words(texts, windows)
                overlay_info["words"] = len(words)
                branded = raw_path.with_name(f"{raw_path.stem}_branded_{uuid.uuid4().hex[:8]}{raw_path.suffix}")
                overlay_page = _CaptionsEnabled(fanpage) if is_asmr and not fanpage.ai_video_captions_enabled else fanpage
                render_branded(str(raw_path), str(branded), overlay_page, words, workdir)
                thumbnail = None
                if fanpage and getattr(fanpage, "ai_video_cover_enabled", True):
                    from app.services.ai_video_cover_build import apply_cover
                    res = apply_cover(job, fanpage, branded, raw_path, workdir)
                    if res:
                        covered_video, cover_jpg = res
                        branded.unlink(missing_ok=True)
                        branded = covered_video
                        thumbnail = cover_jpg
                if thumbnail is None:
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
        elif not tts_failed:
            raw_path = Path(job.video_path)
            workdir = raw_path.parent / f"cover_{job.id}_{uuid.uuid4().hex[:8]}"
            if fanpage and getattr(fanpage, "ai_video_cover_enabled", True):
                from app.services.ai_video_cover_build import apply_cover
                res = apply_cover(job, fanpage, raw_path, raw_path, workdir)
                shutil.rmtree(workdir, ignore_errors=True)
                if res:
                    covered_video, cover_jpg = res
                    s = get_settings()
                    publish_video_path = str(covered_video)
                    publish_video_url = _sibling_url(job.video_url, job.video_path, covered_video, s.storage_base_url)
                    publish_thumbnail_path = str(cover_jpg)
                    publish_thumbnail_url = _sibling_url(job.video_url, job.video_path, cover_jpg, s.storage_base_url)
                    if job.thumbnail_path:
                        Path(job.thumbnail_path).unlink(missing_ok=True)
                    job.video_path, job.video_url = publish_video_path, publish_video_url
                    job.thumbnail_path, job.thumbnail_url = publish_thumbnail_path, publish_thumbnail_url
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


def find_raw_ai_video_path(publish_job, db) -> Path | None:
    """Find existing unbranded raw video file for an ai_video PublishJob."""
    from app.models.ai_video import VideoJob

    candidate_paths = []
    if publish_job.video_path:
        candidate_paths.append(publish_job.video_path)

    video_job = db.query(VideoJob).filter_by(publish_job_id=publish_job.id).order_by(VideoJob.id.desc()).first()
    if video_job and video_job.video_path:
        candidate_paths.append(video_job.video_path)

    for path_str in candidate_paths:
        p = Path(path_str)
        if "_branded_" in p.name:
            raw_name = f"{p.name.split('_branded_')[0]}{p.suffix}"
            raw_p = p.with_name(raw_name)
            if raw_p.is_file():
                return raw_p
        elif p.is_file():
            return p

    if video_job and video_job.video_path:
        parent = Path(video_job.video_path).parent
        if parent.is_dir():
            for match in parent.glob(f"job_{video_job.id}_*.mp4"):
                if "_branded_" not in match.name and match.is_file():
                    return match

    return None


@celery_app.task(name="app.tasks.ai_video_finalize.rerender_ai_video_overlay", queue="video", soft_time_limit=600)
def rerender_ai_video_overlay(publish_job_id: int):
    from app.models.ai_video import VideoIdea, VideoJob
    from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
    from app.models.target_fanpages import PublishMode, TargetFanpage
    from app.config import get_settings

    db = SessionLocal()
    try:
        publish_job = db.query(PublishJob).filter_by(id=publish_job_id).first()
        if not publish_job or publish_job.content_type != ContentType.ai_video:
            return

        fanpage = db.query(TargetFanpage).filter_by(id=publish_job.fanpage_id).first()
        video_job = db.query(VideoJob).filter_by(publish_job_id=publish_job.id).order_by(VideoJob.id.desc()).first()
        idea = db.query(VideoIdea).filter_by(id=video_job.idea_id).first() if video_job and video_job.idea_id else None

        raw_path = find_raw_ai_video_path(publish_job, db)
        if not raw_path or not raw_path.is_file():
            publish_job.status = PublishJobStatus.failed
            publish_job.last_error = "The original AI video file is gone; it cannot be re-rendered"
            db.commit()
            return

        is_asmr = bool(video_job and (video_job.narration or {}).get("engine") == "asmr")
        overlays_enabled = is_asmr or bool(fanpage and (fanpage.ai_video_logo_enabled or fanpage.ai_video_captions_enabled))
        overlay_info = {
            "logo": bool(fanpage and fanpage.ai_video_logo_enabled),
            "captions": is_asmr or bool(fanpage and fanpage.ai_video_captions_enabled),
            "words": 0,
        }

        old_video_path = publish_job.video_path
        old_thumb_path = publish_job.video_thumbnail_path

        if overlays_enabled:
            from app.services.ai_video_overlay import estimate_words, render_branded, speech_windows
            from app.services.fanpage_language import fanpage_language
            from app.services.speech_words import caption_words

            workdir = raw_path.parent / f"overlay_{publish_job.id}_{uuid.uuid4().hex[:8]}"
            try:
                if is_asmr:
                    words = _asmr_caption_words(video_job)
                    overlay_info["timing"] = "asmr"
                elif video_job and (video_job.narration or {}).get("engine") == "tts":
                    words = _tts_caption_words(video_job, fanpage_language(fanpage) if fanpage else None)
                    overlay_info["timing"] = "tts"
                else:
                    variables = idea.variables if idea and isinstance(idea.variables, dict) else {}
                    n_clips = len((video_job.clips if video_job else []) or [])
                    texts = [variables.get(f"narration_{i}", "") for i in range(1, n_clips + 1)] if n_clips else []
                    words = caption_words(str(raw_path), fanpage_language(fanpage) if fanpage else None, texts)
                    overlay_info["timing"] = "voice" if words else "estimate"
                    if not words and n_clips:
                        clip_seconds = video_job.clip_seconds if video_job and video_job.clip_seconds else 4
                        windows = speech_windows(str(raw_path), clip_seconds, len(texts))
                        words = estimate_words(texts, windows)
                overlay_info["words"] = len(words) if words else 0
                branded = raw_path.with_name(f"{raw_path.stem}_branded_{uuid.uuid4().hex[:8]}{raw_path.suffix}")
                overlay_page = _CaptionsEnabled(fanpage) if is_asmr and not fanpage.ai_video_captions_enabled else fanpage
                render_branded(str(raw_path), str(branded), overlay_page, words or [], workdir)
                thumbnail = None
                if fanpage and getattr(fanpage, "ai_video_cover_enabled", True) and video_job:
                    from app.services.ai_video_cover_build import apply_cover
                    res = apply_cover(video_job, fanpage, branded, raw_path, workdir)
                    if res:
                        covered_video, cover_jpg = res
                        branded.unlink(missing_ok=True)
                        branded = covered_video
                        thumbnail = cover_jpg
                if thumbnail is None:
                    thumbnail = branded.with_suffix(".jpg")
                    import subprocess
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(branded), "-frames:v", "1", "-q:v", "3", str(thumbnail)],
                        check=True,
                        timeout=60,
                    )
                s = get_settings()
                publish_video_path = str(branded)
                publish_video_url = _sibling_url(publish_job.video_url, publish_job.video_path or str(raw_path), branded, s.storage_base_url)
                publish_thumbnail_path = str(thumbnail)
                publish_thumbnail_url = _sibling_url(publish_job.video_url, publish_job.video_path or str(raw_path), thumbnail, s.storage_base_url)
            except Exception as exc:
                publish_job.status = PublishJobStatus.pending_review
                publish_job.last_error = f"overlay failed: {exc}"[:2000]
                if video_job and isinstance(video_job.qc, dict):
                    qc = dict(video_job.qc)
                    overlay_info["error"] = str(exc)[:500]
                    qc["overlay"] = overlay_info
                    video_job.qc = qc
                db.commit()
                logger.exception("AI video overlay re-render failed for publish job %s", publish_job_id)
                return
        else:
            video_to_publish = raw_path
            thumbnail = None
            if fanpage and getattr(fanpage, "ai_video_cover_enabled", True) and video_job:
                from app.services.ai_video_cover_build import apply_cover
                workdir = raw_path.parent / f"cover_{publish_job.id}_{uuid.uuid4().hex[:8]}"
                res = apply_cover(video_job, fanpage, raw_path, raw_path, workdir)
                if res:
                    video_to_publish, thumbnail = res
            if thumbnail is None:
                thumbnail = raw_path.with_suffix(".jpg")
                if not thumbnail.is_file():
                    import subprocess
                    subprocess.run(
                        ["ffmpeg", "-v", "error", "-y", "-ss", "3", "-i", str(raw_path), "-frames:v", "1", "-q:v", "3", str(thumbnail)],
                        check=True,
                        timeout=60,
                    )
            s = get_settings()
            publish_video_path = str(video_to_publish)
            publish_video_url = _sibling_url(publish_job.video_url, publish_job.video_path or str(raw_path), video_to_publish, s.storage_base_url)
            publish_thumbnail_path = str(thumbnail)
            publish_thumbnail_url = _sibling_url(publish_job.video_url, publish_job.video_path or str(raw_path), thumbnail, s.storage_base_url)

        if old_video_path and old_video_path != str(raw_path) and old_video_path != publish_video_path:
            Path(old_video_path).unlink(missing_ok=True)
        if old_thumb_path and old_thumb_path != publish_thumbnail_path and old_thumb_path != str(raw_path.with_suffix(".jpg")):
            Path(old_thumb_path).unlink(missing_ok=True)

        publish_job.video_path = publish_video_path
        publish_job.video_url = publish_video_url
        publish_job.video_thumbnail_path = publish_thumbnail_path
        publish_job.video_thumbnail_url = publish_thumbnail_url

        if video_job:
            video_job.video_path = publish_video_path
            video_job.video_url = publish_video_url
            video_job.thumbnail_path = publish_thumbnail_path
            video_job.thumbnail_url = publish_thumbnail_url
            if isinstance(video_job.qc, dict):
                qc = dict(video_job.qc)
                qc["overlay"] = overlay_info
                video_job.qc = qc

        qc_passed = True
        reasons = []
        if video_job and isinstance(video_job.qc, dict):
            qc_passed = bool(video_job.qc.get("passed", True))
            reasons = video_job.qc.get("reasons") or []

        if not qc_passed:
            publish_status = PublishJobStatus.pending_review
            publish_job.last_error = ("QC: " + "; ".join(reasons)) if reasons else "QC failed"
        else:
            if fanpage and fanpage.ai_video_publish_mode == PublishMode.auto:
                publish_status = PublishJobStatus.pending_publish
            else:
                publish_status = PublishJobStatus.pending_review
            publish_job.last_error = None

        publish_job.status = publish_status
        db.commit()

        if publish_status == PublishJobStatus.pending_publish and fanpage and fanpage.ai_video_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job as publish_task
            publish_task.delay(publish_job.id)
    except Exception as exc:
        db.rollback()
        logger.exception("AI video overlay re-render crashed for publish job %s", publish_job_id)
        pj = db.query(PublishJob).filter_by(id=publish_job_id).first()
        if pj:
            pj.status = PublishJobStatus.failed
            pj.last_error = f"rerender error: {type(exc).__name__}"
            db.commit()
    finally:
        db.close()
