"""Fan-out task — creates one PublishJob per active fanpage for a stored post."""

import logging
import random
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal

# Stagger between fanpages sharing the same IG source (seconds) — 1 to 25
# minutes. Each fanpage draws independently (not slot * range) so the gaps
# themselves don't form a predictable, obviously-formulaic pattern.
_FANPAGE_STAGGER_MIN = 60
_FANPAGE_STAGGER_MAX = 1500


def _fanpage_stagger(slot: int) -> int:
    return 0 if slot == 0 else random.randint(_FANPAGE_STAGGER_MIN, _FANPAGE_STAGGER_MAX)


def fanout_link(db, post, link, countdown: int, radar_decision_id: int | None = None) -> bool:
    """Executes fan-out for a single source link (recreate or plain repost).
    
    Returns True if a job was created or recreating was scheduled.
    """
    from app.models.publish_jobs import PublishJob, PublishJobStatus

    # Mode 1 gate: skip fanpages that have disabled IG repost
    if not link.fanpage.mode1_ig_repost_enabled:
        logger.info(
            "Post %d: skipping fanpage %d — mode1_ig_repost_enabled is false",
            post.id, link.fanpage_id,
        )
        return False

    # Idempotency: skip if job already exists
    existing = db.query(PublishJob).filter_by(
        post_id=post.id, fanpage_id=link.fanpage_id
    ).first()
    if existing:
        return False

    # Mode 3: classify the IG post image and rebuild it on a quote/news
    # template instead of reposting the original. Per-source override
    # (link.ig_recreate_enabled) wins when set; otherwise inherit the
    # fanpage's blanket setting — lets a fanpage recreate some sources'
    # posts while reposting others plain (caption-only).
    recreate = link.ig_recreate_enabled
    if recreate is None:
        recreate = link.fanpage.ig_recreate_enabled
    if recreate:
        from app.tasks.ig_recreate import recreate_post_for_fanpage
        # Same stagger as the plain-repost path below — without it,
        # every fanpage recreating the same source's post would render
        # and (if auto-publish) go live within seconds of each other.
        recreate_post_for_fanpage.apply_async(args=[post.id, link.fanpage_id], kwargs={"radar_decision_id": radar_decision_id}, countdown=countdown)
        return True

    job = PublishJob(
        post_id=post.id,
        fanpage_id=link.fanpage_id,
        status=PublishJobStatus.pending_caption,
    )
    db.add(job)
    db.flush()

    if radar_decision_id:
        from app.models.radar import RadarStoryDecision, DecisionStatus
        decision = db.query(RadarStoryDecision).filter_by(id=radar_decision_id).first()
        if decision:
            decision.publish_job_id = job.id
            decision.status = DecisionStatus.CREATED

    # Commit BEFORE queueing: with countdown=0 the caption worker can pick the
    # task up immediately and must find the committed job row.
    db.commit()

    # Stagger caption generation (and therefore publishing) so fanpages
    # sharing the same IG source don't all post at exactly the same time.
    from app.tasks.ai_generator import generate_caption_for_job
    generate_caption_for_job.apply_async(args=[job.id], countdown=countdown)

    if countdown:
        logger.info(
            "Job %d (fanpage=%d) delayed %ds to avoid simultaneous posting",
            job.id, link.fanpage_id, countdown,
        )
    return True


logger = logging.getLogger(__name__)


@celery_app.task(name="app.tasks.fan_out.create_fanout_jobs", bind=True, max_retries=2)
def create_fanout_jobs(self, post_id: int):
    """Find all active fanpages sourcing this post's IG account and create PublishJobs."""
    db = SessionLocal()
    try:
        from app.models.posts import Post, PostStatus
        from app.models.fanpage_sources import FanpageSource
        from app.models.target_fanpages import TargetFanpage

        post = db.query(Post).filter_by(id=post_id).first()
        if not post:
            return

        # Find active fanpages that subscribe to this IG source
        fanpage_links = (
            db.query(FanpageSource)
            .join(TargetFanpage, TargetFanpage.id == FanpageSource.fanpage_id)
            .filter(
                FanpageSource.ig_source_id == post.ig_source_id,
                FanpageSource.is_active == True,
                TargetFanpage.is_active == True,
                TargetFanpage.is_connected == True,
            )
            .all()
        )

        created = 0
        slot = 0  # stagger slot index for fanpages sharing this source
        for link in fanpage_links:
            # Skip viral_only links; they wait for radar dispatcher.
            if getattr(link, "trigger", "every_post") == "viral_only":
                logger.info(
                    "Post %d: skipping fanpage %d — link trigger is viral_only",
                    post_id, link.fanpage_id,
                )
                continue

            stagger = _fanpage_stagger(slot)
            if fanout_link(db, post, link, countdown=stagger):
                # When fanout_link returns True, we might have created a PublishJob
                # (if Mode 1). We should commit here inside the loop for the newly created job
                # to be persisted immediately, just like the old code.
                db.commit()
                created += 1
                slot += 1

        post.status = PostStatus.pending_fanout
        db.commit()

        logger.info("Post %d: created %d publish jobs for %d fanpages", post_id, created, len(fanpage_links))

    except Exception as exc:
        db.rollback()
        logger.error("Fan-out error for post %d: %s", post_id, exc, exc_info=True)
        raise self.retry(exc=exc, countdown=60)
    finally:
        db.close()


@celery_app.task(name="app.tasks.fan_out.recover_stuck_posts")
def recover_stuck_posts():
    """Re-trigger fan-out for posts stuck in 'stored' status with no publish jobs.

    Runs every 15 minutes to catch cases where fan-out was never triggered
    (e.g., worker crashed mid-chain or fan-out task was dropped from Redis).
    """
    db = SessionLocal()
    try:
        from app.models.posts import Post, PostStatus
        from app.models.publish_jobs import PublishJob
        from sqlalchemy import exists

        cutoff = datetime.now(timezone.utc) - timedelta(minutes=15)

        stuck_ids = (
            db.query(Post.id)
            .filter(
                Post.status == PostStatus.stored,
                Post.updated_at < cutoff,
                ~exists().where(PublishJob.post_id == Post.id),
            )
            .all()
        )

        for (post_id,) in stuck_ids:
            create_fanout_jobs.delay(post_id)
            logger.info("Recovery: re-triggering fan-out for stuck post %d", post_id)

        if stuck_ids:
            logger.warning("Recovery: found %d stuck stored posts — fan-out re-queued", len(stuck_ids))

    finally:
        db.close()
