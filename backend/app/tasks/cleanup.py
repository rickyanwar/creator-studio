"""Cleanup task — deletes media files after publishing or when stuck."""

import logging
import shutil
from datetime import datetime, timezone, timedelta
from pathlib import Path

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Delete files from stuck/orphaned posts after this long regardless
_STUCK_POST_MAX_AGE_HOURS = 12

# Delete rendered design PNGs (Mode 2/3/4/5 job output) once they're this
# old. Added 2026-08-31 — unlike cleanup_old_media below (Mode 1's posts/
# folder, cleaned a few hours after publish), design output had NO
# retention at all before this: designs/ had grown to 16GB/4429 files
# spanning the app's whole ~33-day history, ~485MB/day and rising with
# Mode 5 now active. The actual post already lives on Facebook/Instagram —
# the local PNG only serves the app's own History/Queue preview, which has
# no reason to reach back this far. 21 days matches the one-time backlog
# cleanup run the same day (user's choice — freed 5.48GB/1475 files).
_DESIGN_RETENTION_DAYS = 21
# Owner 2026-10-14: media of a successful post is deleted 7 days after publish (it is already on Facebook). Failed/skipped media keeps the longer retention for reposts.
_PUBLISHED_DESIGN_RETENTION_DAYS = 7
_AI_VIDEO_RETENTION_DAYS = 14
_PUBLISHED_AI_VIDEO_RETENTION_DAYS = 7
_THUMB_RETENTION_DAYS = 30

# Repliz and the phone fetch post images at go-live, not at scheduling time.
_KEEP_AFTER_GO_LIVE = timedelta(hours=6)
_PENDING_JOB_KEEP_HOURS = 48
_LIVE_DELIVERY_STATES = (
    "waiting_for_slot",
    "scheduled",
    "publishing",
    "waiting_for_device",
    "uncertain",
    "needs_human",
    "repliz_scheduled",
)


def _post_media_still_needed(db, jobs, now) -> bool:
    """True while any job of the post may still publish its images (naive UTC `now`)."""
    if not jobs:
        return False

    now_naive = now.astimezone(timezone.utc).replace(tzinfo=None) if now.tzinfo is not None else now

    for job in jobs:
        if job.scheduled_for:
            sf = job.scheduled_for
            if sf.tzinfo is not None:
                sf = sf.astimezone(timezone.utc).replace(tzinfo=None)
            if sf + _KEEP_AFTER_GO_LIVE > now_naive:
                return True

        if job.status in ("pending_publish", "publishing"):
            if job.created_at is None:
                return True
            ca = job.created_at
            if ca.tzinfo is not None:
                ca = ca.astimezone(timezone.utc).replace(tzinfo=None)
            if (now_naive - ca) <= timedelta(hours=_PENDING_JOB_KEEP_HOURS):
                return True

    job_ids = [j.id for j in jobs if j.id is not None]
    if not job_ids:
        return False

    from sqlalchemy import or_
    from app.models.social_publishing import PublishDelivery

    cutoff = now_naive - _KEEP_AFTER_GO_LIVE
    live_delivery = (
        db.query(PublishDelivery.id)
        .filter(
            PublishDelivery.publish_job_id.in_(job_ids),
            PublishDelivery.state.in_(_LIVE_DELIVERY_STATES),
            or_(
                PublishDelivery.scheduled_for.is_(None),
                PublishDelivery.scheduled_for > cutoff,
            ),
        )
        .first()
    )
    return live_delivery is not None


@celery_app.task(name="app.tasks.cleanup.cleanup_old_media")
def cleanup_old_media():
    """Remove media files for:
    - Posts where all jobs published and cleanup_at passed (6h after publish)
    - Posts where all jobs failed/skipped (no point keeping files)
    - Posts stuck in stored/pending_fanout for more than 12h
    - never while a job may still publish (go-live + 6 h, live deliveries, pending jobs < 48 h)
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cleaned = 0

    try:
        from app.models.posts import Post, PostStatus
        from app.models.publish_jobs import PublishJob, PublishJobStatus

        candidates = (
            db.query(Post)
            .filter(
                Post.status.in_([
                    PostStatus.done,
                    PostStatus.stored,
                    PostStatus.pending_fanout,
                ]),
                Post.image_local_paths != "{}",
            )
            .all()
        )

        for post in candidates:
            jobs = db.query(PublishJob).filter_by(post_id=post.id).all()
            should_clean = False

            if post.status == PostStatus.done:
                # All jobs terminal + cleanup_at passed for published ones
                all_terminal = all(
                    j.status in (PublishJobStatus.published, PublishJobStatus.failed, PublishJobStatus.skipped)
                    for j in jobs
                )
                published_jobs = [j for j in jobs if j.status == PublishJobStatus.published]
                cleanup_due = all(j.cleanup_at and j.cleanup_at <= now for j in published_jobs)
                should_clean = all_terminal and (not published_jobs or cleanup_due)

            elif jobs and all(
                j.status in (PublishJobStatus.failed, PublishJobStatus.skipped) for j in jobs
            ):
                # All jobs failed/skipped — files will never be used
                should_clean = True

            elif post.crawled_at and (now - post.crawled_at).total_seconds() > _STUCK_POST_MAX_AGE_HOURS * 3600:
                # Stuck in stored/pending_fanout for too long — orphaned
                should_clean = True

            if not should_clean:
                continue

            if _post_media_still_needed(db, jobs, now):
                continue

            post_dir = Path(settings.storage_base_path) / "posts" / str(post.uuid)
            if post_dir.exists():
                try:
                    shutil.rmtree(post_dir)
                    logger.info("Cleaned media for post %d (%s) [status=%s]", post.id, post.uuid, post.status)
                except OSError as exc:
                    logger.error("Failed to delete %s: %s", post_dir, exc)
                    continue

            post.image_local_paths = []
            post.image_public_urls = []
            post.status = PostStatus.cleaned
            db.add(post)
            cleaned += 1

        db.commit()
        logger.info("Cleanup complete: %d posts cleaned", cleaned)

    except Exception as exc:
        db.rollback()
        logger.error("Cleanup task error: %s", exc, exc_info=True)
    finally:
        db.close()


@celery_app.task(name="app.tasks.cleanup.cleanup_old_designs")
def cleanup_old_designs():
    """Delete design_image_path files (Mode 2/3/4/5 rendered PNGs) for
    published jobs older than _PUBLISHED_DESIGN_RETENTION_DAYS (7) days, and failed/skipped jobs older than
    _DESIGN_RETENTION_DAYS. Age is measured from published_at when the job
    actually went live, else from updated_at (failed/skipped jobs have no
    published_at). Clears design_image_path/design_image_url on the row too,
    so the Queue/History UI doesn't try to load a file that's gone — same
    pattern cleanup_old_media uses for Post.image_local_paths above."""
    db = SessionLocal()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_DESIGN_RETENTION_DAYS)).replace(tzinfo=None)
    published_cutoff = (datetime.now(timezone.utc) - timedelta(days=_PUBLISHED_DESIGN_RETENTION_DAYS)).replace(tzinfo=None)
    cleaned = 0

    try:
        from sqlalchemy import and_, or_
        from app.models.publish_jobs import PublishJob, PublishJobStatus

        candidates = (
            db.query(PublishJob)
            .filter(
                PublishJob.design_image_path.isnot(None),
                PublishJob.status.in_([
                    PublishJobStatus.published, PublishJobStatus.failed, PublishJobStatus.skipped,
                ]),
                or_(
                    and_(PublishJob.status == PublishJobStatus.published,
                         PublishJob.published_at.isnot(None), PublishJob.published_at <= published_cutoff),
                    and_(PublishJob.status == PublishJobStatus.published,
                         PublishJob.published_at.is_(None), PublishJob.updated_at <= cutoff),
                    and_(PublishJob.status.in_([PublishJobStatus.failed, PublishJobStatus.skipped]),
                         PublishJob.updated_at <= cutoff),
                ),
            )
            .all()
        )

        for job in candidates:
            try:
                Path(job.design_image_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.error("Failed to delete design %s: %s", job.design_image_path, exc)
                continue
            job.design_image_path = None
            job.design_image_url = None
            cleaned += 1

        db.commit()
        if cleaned:
            logger.info("Design cleanup: removed %d file(s) (published > %d day, other > %d days)", cleaned, _PUBLISHED_DESIGN_RETENTION_DAYS, _DESIGN_RETENTION_DAYS)

    except Exception as exc:
        db.rollback()
        logger.error("Design cleanup task error: %s", exc, exc_info=True)
    finally:
        db.close()


@celery_app.task(name="app.tasks.cleanup.cleanup_old_ai_videos")
def cleanup_old_ai_videos():
    """Delete terminal Mode 8 video files older than the retention period."""
    db = SessionLocal()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_AI_VIDEO_RETENTION_DAYS)).replace(tzinfo=None)
    published_cutoff = (datetime.now(timezone.utc) - timedelta(days=_PUBLISHED_AI_VIDEO_RETENTION_DAYS)).replace(tzinfo=None)
    cleaned = 0
    media_dir = (Path(settings.storage_base_path) / "ai_videos").resolve()

    def safe_path(raw_path):
        if not raw_path:
            return None
        path = Path(raw_path)
        try:
            resolved = path.resolve()
            resolved.relative_to(media_dir)
            return resolved
        except ValueError:
            logger.warning("Skipping AI video path outside media root: %s", raw_path)
            return None

    def delete_paths(paths):
        resolved_paths = []
        for raw_path in paths:
            if raw_path:
                resolved = safe_path(raw_path)
                if resolved is None:
                    return False
                resolved_paths.append(resolved)
        try:
            for path in resolved_paths:
                path.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("Failed to delete AI video file: %s", exc)
            return False
        return True

    try:
        from sqlalchemy import and_, or_, func
        from app.models.ai_video import VideoJob
        from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus

        publish_candidates = (
            db.query(PublishJob)
            .filter(
                PublishJob.content_type == ContentType.ai_video,
                PublishJob.video_path.isnot(None),
                PublishJob.status.in_([
                    PublishJobStatus.published, PublishJobStatus.failed, PublishJobStatus.skipped,
                ]),
                or_(
                    and_(PublishJob.status == PublishJobStatus.published,
                         PublishJob.published_at.isnot(None), PublishJob.published_at <= published_cutoff),
                    and_(PublishJob.status == PublishJobStatus.published,
                         PublishJob.published_at.is_(None), PublishJob.updated_at <= cutoff),
                    and_(PublishJob.status.in_([PublishJobStatus.failed, PublishJobStatus.skipped]),
                         PublishJob.updated_at <= cutoff),
                ),
            )
            .all()
        )
        for job in publish_candidates:
            if not delete_paths((job.video_path, job.video_thumbnail_path)):
                continue
            job.video_path = job.video_url = None
            job.video_thumbnail_path = job.video_thumbnail_url = None
            cleaned += 1

        video_candidates = db.query(VideoJob).filter(
            or_(VideoJob.video_path.isnot(None), VideoJob.thumbnail_path.isnot(None)),
        ).all()
        terminal_statuses = {PublishJobStatus.published, PublishJobStatus.failed, PublishJobStatus.skipped}
        for job in video_candidates:
            if job.status in ("failed", "cancelled"):
                age = job.finished_at or job.updated_at or job.created_at
                eligible = age <= cutoff
            elif job.status == "done" and job.publish_job_id:
                publish_job = db.get(PublishJob, job.publish_job_id)
                age = None
                limit = cutoff
                if publish_job and publish_job.status in terminal_statuses:
                    age = publish_job.published_at or publish_job.updated_at
                    limit = published_cutoff if (publish_job.status == PublishJobStatus.published and publish_job.published_at) else cutoff
                eligible = age is not None and age <= limit
            else:
                eligible = False
            if not eligible or not delete_paths((job.video_path, job.thumbnail_path)):
                continue
            job.video_path = job.video_url = None
            job.thumbnail_path = job.thumbnail_url = None
            cleaned += 1

        db.commit()
        logger.info("AI video cleanup: removed %d file(s) (published > %d days, other > %d days)", cleaned, _PUBLISHED_AI_VIDEO_RETENTION_DAYS, _AI_VIDEO_RETENTION_DAYS)
    except Exception as exc:
        db.rollback()
        logger.error("AI video cleanup task error: %s", exc, exc_info=True)
    finally:
        db.close()


@celery_app.task(name="app.tasks.cleanup.cleanup_old_thumbs")
def cleanup_old_thumbs() -> int:
    """Delete cached thumbnails older than 30 days from storage_base_path/thumbs/."""
    import time
    thumbs_dir = Path(settings.storage_base_path) / "thumbs"
    if not thumbs_dir.is_dir():
        return 0

    cutoff_ts = time.time() - (_THUMB_RETENTION_DAYS * 86400)
    cleaned = 0

    try:
        for file_path in thumbs_dir.rglob("*.webp"):
            if not file_path.is_file():
                continue
            try:
                if file_path.stat().st_mtime < cutoff_ts:
                    file_path.unlink(missing_ok=True)
                    cleaned += 1
            except OSError as exc:
                logger.warning("Failed to delete old thumbnail %s: %s", file_path, exc)

        logger.info("Thumb cleanup: removed %d cached thumbnail(s) older than %d days", cleaned, _THUMB_RETENTION_DAYS)
    except Exception as exc:
        logger.error("Thumbnail cleanup task error: %s", exc, exc_info=True)

    return cleaned
