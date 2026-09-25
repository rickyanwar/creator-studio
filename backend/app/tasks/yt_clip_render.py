"""Mode 7 clip rendering (the `video` queue) + video file retention.

render_youtube_clip: download just the clip's section → captions (ASS) →
per-shot reframe → ONE encode with captions + watermark → MP4 + thumbnail →
pending_publish (auto fanpages publish straight on). Runs on the dedicated
worker-video service (concurrency 1) so a render never starves the main
worker. Idempotent: every attempt works in its own fresh folder and
overwrites its output, so a deploy killing a render mid-way just means the
job gets re-rendered (acks_late re-queues it; recover_stuck_renders resets
a stale 'rendering' claim).
"""

import base64
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import get_settings
from app.database import SessionLocal
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_MAX_RENDER_ATTEMPTS = 3
# Repliz may fetch the MP4 at go-live, not at scheduling time — keep it well
# past scheduled_for, then delete (a clip is ~20-60 MB).
_VIDEO_KEEP_AFTER_GO_LIVE = timedelta(hours=48)
_FAILED_VIDEO_KEEP = timedelta(hours=48)
_THUMBNAIL_KEEP = timedelta(days=21)
_WORKDIR_MAX_AGE = timedelta(hours=24)
_SUBS_MAX_AGE = timedelta(days=14)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _claim(db, job_id: int) -> bool:
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType

    claimed = (
        db.query(PublishJob)
        .filter(
            PublishJob.id == job_id,
            PublishJob.status == PublishJobStatus.pending_design,
            PublishJob.content_type == ContentType.youtube_clip,
        )
        .update({"status": PublishJobStatus.rendering}, synchronize_session=False)
    )
    db.commit()
    return bool(claimed)


def _load_words(db, job, workdir: Path):
    """The source video's word timings — from the shared subtitle cache, or
    re-fetched if the cache was cleaned up while the idea waited."""
    from app.models.yt_videos import YtVideo
    from app.services import yt_downloader as yd
    from app.services.yt_transcript import parse_json3

    video = db.query(YtVideo).filter_by(fanpage_id=job.fanpage_id, video_id=job.yt_video_id).first()
    if video and video.words_path and Path(video.words_path).exists():
        return parse_json3(Path(video.words_path).read_bytes())[1], video
    meta, data = yd.fetch_video(db, job.yt_video_id, workdir)
    if not data:
        return [], video
    if video:
        from app.tasks.yt_clip import _store_subtitles
        video.words_path = str(_store_subtitles(job.yt_video_id, meta.sub_track, data))
        db.commit()
    return parse_json3(data)[1], video


def _watermark(fanpage, workdir: Path) -> tuple[Path | None, str | None]:
    """(logo PNG path, text) — the logo wins when the fanpage has one."""
    from app.services.design_images import watermark_datauri

    if not fanpage.yt_clip_watermark:
        return None, None
    uri = watermark_datauri(fanpage)
    if uri:
        path = workdir / "watermark.png"
        path.write_bytes(base64.b64decode(uri.split(",", 1)[1]))
        return path, None
    return None, (fanpage.watermark_text or None)


def _fail_or_retry(db, job, error: str, *, permanent: bool = False) -> None:
    from app.models.publish_jobs import PublishJobStatus

    job.attempt_count = (job.attempt_count or 0) + 1
    job.last_error = error[:2000]
    job.status = (
        PublishJobStatus.failed
        if permanent or job.attempt_count >= _MAX_RENDER_ATTEMPTS
        else PublishJobStatus.pending_design
    )
    db.commit()
    logger.warning("yt_clip_render: job %d attempt %d → %s: %s", job.id, job.attempt_count, job.status.value, error[:300])


def _render(db, job, fanpage, workdir: Path) -> None:
    from app.services import yt_downloader as yd
    from app.services.video_captions import FONTS_DIR, write_ass
    from app.services.video_reframe import ClipSpec, render_clip

    s = get_settings()
    start, end = float(job.clip_start_s), float(job.clip_end_s)
    words, video = _load_words(db, job, workdir)
    section = yd.download_clip_source(db, job.yt_video_id, start, end, video.duration_s if video else None, workdir)
    ass_path = workdir / "captions.ass"
    has_captions = bool(fanpage.yt_clip_captions and words and write_ass(words, start, end, ass_path))
    wm_png, wm_text = _watermark(fanpage, workdir)

    videos_dir = Path(s.storage_base_path) / "videos"
    videos_dir.mkdir(parents=True, exist_ok=True)
    out = videos_dir / f"job_{job.id}_{uuid.uuid4().hex[:8]}.mp4"
    result = render_clip(ClipSpec(
        src=section.path, seek_s=start - section.offset_s, duration_s=end - start, out=out,
        ass_path=ass_path if has_captions else None, fonts_dir=FONTS_DIR,
        watermark_png=wm_png, watermark_text=wm_text,
    ))
    base_url = s.storage_base_url.rstrip("/")
    job.video_path, job.video_url = str(out), f"{base_url}/videos/{out.name}"
    job.video_thumbnail_path, job.video_thumbnail_url = str(result.thumbnail), f"{base_url}/videos/{result.thumbnail.name}"
    job.video_duration_s = round(result.duration_s, 2)


@celery_app.task(name="app.tasks.yt_clip_render.render_youtube_clip", soft_time_limit=1500, time_limit=1620)
def render_youtube_clip(job_id: int):
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.target_fanpages import PublishMode
    from app.services import yt_downloader as yd

    db = SessionLocal()
    from app.tasks.yt_clip import yt_private_dir

    workdir = yt_private_dir("work") / f"job_{job_id}_{uuid.uuid4().hex[:6]}"
    try:
        if not _claim(db, job_id):
            return
        job = db.query(PublishJob).filter_by(id=job_id).first()
        fanpage = job.fanpage
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            _render(db, job, fanpage, workdir)
        except yd.YouTubeBlockedError as exc:
            job.status, job.last_error = PublishJobStatus.pending_design, f"YouTube paused: {str(exc)[:500]}"
            db.commit()
            return
        except yd.YouTubeUnavailableError as exc:
            _fail_or_retry(db, job, f"video unavailable: {exc}", permanent=True)
            return
        job.status, job.last_error = PublishJobStatus.pending_publish, None
        db.commit()
        logger.info("yt_clip_render: job %d rendered → %s (%.1fs)", job.id, job.video_url, job.video_duration_s or 0)
        if fanpage.yt_clip_publish_mode == PublishMode.auto:
            from app.tasks.publisher import publish_job
            publish_job.delay(job.id)
    except Exception as exc:
        db.rollback()
        job = db.query(PublishJob).filter_by(id=job_id).first()
        if job and job.status == PublishJobStatus.rendering:
            _fail_or_retry(db, job, str(exc))
        logger.error("yt_clip_render: job %d crashed: %s", job_id, exc, exc_info=True)
    finally:
        yd.cleanup_workdir(workdir)
        db.close()


# ── retention ────────────────────────────────────────────────────────────────

def _unlink(path: str | None) -> None:
    if path:
        Path(path).unlink(missing_ok=True)


def _cleanup_job_videos(db) -> int:
    from sqlalchemy import and_, or_
    from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType

    now = _now()
    jobs = db.query(PublishJob).filter(
        PublishJob.content_type == ContentType.youtube_clip,
        PublishJob.video_path.isnot(None),
        or_(
            and_(PublishJob.status == PublishJobStatus.published,
                 PublishJob.scheduled_for <= now - _VIDEO_KEEP_AFTER_GO_LIVE),
            and_(PublishJob.status.in_([PublishJobStatus.failed, PublishJobStatus.skipped]),
                 PublishJob.updated_at <= now - _FAILED_VIDEO_KEEP),
            and_(PublishJob.is_deleted == True, PublishJob.updated_at <= now - _FAILED_VIDEO_KEEP),  # noqa: E712
        ),
    ).all()
    for job in jobs:
        _unlink(job.video_path)
        job.video_path, job.video_url = None, None
    old_thumbs = db.query(PublishJob).filter(
        PublishJob.content_type == ContentType.youtube_clip,
        PublishJob.video_thumbnail_path.isnot(None), PublishJob.video_path.is_(None),
        PublishJob.updated_at <= now - _THUMBNAIL_KEEP,
    ).all()
    for job in old_thumbs:
        _unlink(job.video_thumbnail_path)
        job.video_thumbnail_path, job.video_thumbnail_url = None, None
    db.commit()
    return len(jobs)


def _cleanup_old_files(root: Path, max_age: timedelta, pattern: str) -> int:
    import shutil

    if not root.exists():
        return 0
    cutoff = datetime.now().timestamp() - max_age.total_seconds()
    removed = 0
    for p in root.glob(pattern):
        if p.stat().st_mtime < cutoff:
            shutil.rmtree(p, ignore_errors=True) if p.is_dir() else p.unlink(missing_ok=True)
            removed += 1
    return removed


@celery_app.task(name="app.tasks.yt_clip_render.cleanup_old_videos")
def cleanup_old_videos():
    """Daily: rendered MP4s 48h after go-live (failed/deleted ones 48h after
    their last change), thumbnails after 21 days, orphaned work folders
    after 24h, cached subtitle tracks after 14 days (a render whose idea
    waited longer just re-fetches them)."""
    db = SessionLocal()
    try:
        from app.tasks.yt_clip import yt_private_dir

        videos = _cleanup_job_videos(db)
        work = _cleanup_old_files(yt_private_dir("work"), _WORKDIR_MAX_AGE, "*")
        subs = _cleanup_old_files(yt_private_dir("subs"), _SUBS_MAX_AGE, "*.json3")
        if videos or work or subs:
            logger.info("yt_clip cleanup: %d video(s), %d work dir(s), %d subtitle file(s)", videos, work, subs)
    except Exception as exc:
        db.rollback()
        logger.error("yt_clip cleanup failed: %s", exc, exc_info=True)
    finally:
        db.close()
