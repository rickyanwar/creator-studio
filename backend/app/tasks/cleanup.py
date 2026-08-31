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


@celery_app.task(name="app.tasks.cleanup.cleanup_old_media")
def cleanup_old_media():
    """Remove media files for:
    - Posts where all jobs published and cleanup_at passed (6h after publish)
    - Posts where all jobs failed/skipped (no point keeping files)
    - Posts stuck in editing_image/stored/pending_fanout for more than 12h
    """
    db = SessionLocal()
    now = datetime.now(timezone.utc)
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
                    PostStatus.editing_image,
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

            elif post.crawled_at and (now - post.crawled_at.replace(tzinfo=timezone.utc)).total_seconds() > _STUCK_POST_MAX_AGE_HOURS * 3600:
                # Stuck in stored/pending_fanout for too long — orphaned
                should_clean = True

            if not should_clean:
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
    terminal jobs older than _DESIGN_RETENTION_DAYS. Age is measured from
    published_at when the job actually went live, else from updated_at
    (failed/skipped jobs have no published_at). Clears design_image_path/
    design_image_url on the row too, so the Queue/History UI doesn't try to
    load a file that's gone — same pattern cleanup_old_media uses for
    Post.image_local_paths above."""
    db = SessionLocal()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=_DESIGN_RETENTION_DAYS)).replace(tzinfo=None)
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
                    and_(PublishJob.published_at.isnot(None), PublishJob.published_at <= cutoff),
                    and_(PublishJob.published_at.is_(None), PublishJob.updated_at <= cutoff),
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
            logger.info("Design cleanup: removed %d file(s) older than %d days", cleaned, _DESIGN_RETENTION_DAYS)

    except Exception as exc:
        db.rollback()
        logger.error("Design cleanup task error: %s", exc, exc_info=True)
    finally:
        db.close()
