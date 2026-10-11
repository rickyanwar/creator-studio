import logging
import random
import uuid
import redis
from datetime import datetime, timezone, timedelta

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings
from sqlalchemy import func
from sqlalchemy.orm import selectinload
from app.tasks.radar_locks import _RELEASE_LUA

from app.models.radar import RadarStoryDecision, DecisionStatus, RadarStory
from app.models.settings import Settings
from app.models.fanpage_sources import FanpageSource
from app.models.ig_sources import IGSource
from app.models.posts import Post, PostStatus
from app.models.target_fanpages import TargetFanpage
from app.models.publish_jobs import PublishJob
from app.services.radar_clustering import token_set, jaccard
from app.tasks.fan_out import fanout_link
from app.tasks.ig_recreate import recreate_post_for_fanpage
from app.tasks.image_saver import download_ig_image

logger = logging.getLogger(__name__)

WIB = timezone(timedelta(hours=7))
app_settings = get_settings()

def _redis():
    return redis.from_url(app_settings.redis_url, decode_responses=True)

def _wib_day_bounds_utc(now_utc: datetime) -> tuple[datetime, datetime]:
    from app.services.radar_time import to_naive_utc
    day_wib = now_utc.replace(tzinfo=timezone.utc).astimezone(WIB)
    start_wib = day_wib.replace(hour=0, minute=0, second=0, microsecond=0)
    end_wib = start_wib + timedelta(days=1)
    return (
        to_naive_utc(start_wib),
        to_naive_utc(end_wib),
    )


@celery_app.task(name="app.tasks.radar_dispatch.radar_dispatch_tick", bind=True)
def radar_dispatch_tick(self):
    r = _redis()
    lock_key = "radar:dispatch_running"
    token = str(uuid.uuid4())
    if not r.set(lock_key, token, nx=True, ex=55):
        logger.info("radar_dispatch_tick already running, skipping")
        return

    db = SessionLocal()
    try:
        settings = db.query(Settings).filter_by(id=1).first()
        if not settings:
            return
            
        sharing_mode = getattr(settings, 'radar_story_sharing', 'shared')
        share_max = getattr(settings, 'radar_share_max', 0)
        news_stagger_max = getattr(settings, 'radar_news_stagger_max_min', 10)

        # Find all stories with queued decisions, oldest first
        stories = (
            db.query(RadarStory)
            .options(selectinload(RadarStory.posts))
            .join(RadarStoryDecision)
            .filter(RadarStoryDecision.status == DecisionStatus.QUEUED)
            .order_by(RadarStory.created_at.asc())
            .distinct()
            .all()
        )

        for story in stories:
            try:
                _process_story(db, story, sharing_mode, share_max, news_stagger_max)
            except Exception as e:
                db.rollback()
                logger.error("Failed to process story %d: %s", story.id, e, exc_info=True)
                # Mark queued decisions failed
                decisions = db.query(RadarStoryDecision).filter(
                    RadarStoryDecision.story_id == story.id,
                    RadarStoryDecision.status == DecisionStatus.QUEUED
                ).all()
                for d in decisions:
                    d.status = DecisionStatus.FAILED
                    d.reason = "crash during process_story"
                db.commit()

    finally:
        db.close()
        r.eval(_RELEASE_LUA, 1, lock_key, token)


def _process_story(db, story, sharing_mode: str, share_max: int, news_stagger_max: int):
    # Get queued decisions for this story
    decisions = (
        db.query(RadarStoryDecision)
        .filter(
            RadarStoryDecision.story_id == story.id,
            RadarStoryDecision.status == DecisionStatus.QUEUED
        )
        .all()
    )
    if not decisions:
        return

    from app.services.radar_time import utcnow_naive
    now_utc = utcnow_naive()
    day_start_utc, day_end_utc = _wib_day_bounds_utc(now_utc)

    # 1. Order candidate fanpages
    # fewest `created` radar decisions in the last 24 h -> oldest last radar post -> fanpage id
    def get_sort_key(d: RadarStoryDecision):
        # created today
        created_today = db.query(func.count(RadarStoryDecision.id)).filter(
            RadarStoryDecision.fanpage_id == d.fanpage_id,
            RadarStoryDecision.status.in_([DecisionStatus.CREATED, DecisionStatus.DISPATCHED]),
            RadarStoryDecision.created_at >= day_start_utc,
            RadarStoryDecision.created_at < day_end_utc
        ).scalar() or 0

        # oldest last radar post (based on PublishJob created from RadarStoryDecision?)
        # Let's just use the latest created_at of CREATED/DISPATCHED decision for this fanpage
        last_decision = db.query(func.max(RadarStoryDecision.created_at)).filter(
            RadarStoryDecision.fanpage_id == d.fanpage_id,
            RadarStoryDecision.status.in_([DecisionStatus.CREATED, DecisionStatus.DISPATCHED])
        ).scalar()
        last_time = last_decision or datetime.min

        return (created_today, last_time, d.fanpage_id)

    decisions.sort(key=get_sort_key)

    # 2. Sharing
    if sharing_mode == "exclusive":
        kept_decisions = decisions[:1]
        for d in decisions[1:]:
            d.status = DecisionStatus.SKIPPED_DEDUP
            d.reason = "exclusive sharing"
        db.commit()
    elif share_max > 0:
        kept_decisions = decisions[:share_max]
        for d in decisions[share_max:]:
            d.status = DecisionStatus.SKIPPED_DEDUP
            d.reason = f"share_max {share_max} reached"
        db.commit()
    else:
        kept_decisions = decisions

    # 3. Per fanpage checks
    best_post = next((p for p in story.posts if p.id == story.best_post_id), None)
    if not best_post:
        best_post = story.posts[0] if story.posts else None

    if not best_post:
        # Cannot process without posts
        for d in kept_decisions:
            d.status = DecisionStatus.FAILED
            d.reason = "no posts in story"
        db.commit()
        return

    slot = 0
    for d in kept_decisions:
        # daily cap check
        fanpage = db.query(TargetFanpage).filter_by(id=d.fanpage_id).first()
        if not fanpage:
            d.status = DecisionStatus.FAILED
            d.reason = "fanpage not found"
            db.commit()
            continue

        created_today = db.query(func.count(RadarStoryDecision.id)).filter(
            RadarStoryDecision.fanpage_id == d.fanpage_id,
            RadarStoryDecision.status.in_([DecisionStatus.CREATED, DecisionStatus.DISPATCHED]),
            RadarStoryDecision.created_at >= day_start_utc,
            RadarStoryDecision.created_at < day_end_utc
        ).scalar() or 0

        if created_today >= fanpage.radar_daily_max:
            d.status = DecisionStatus.SKIPPED_DAILY_MAX
            d.reason = "daily cap reached"
            db.commit()
            continue

        # dedup check
        # PublishJob (any mode) in the last 48 h whose post's ig_post_url contains any member shortcode, 
        # OR whose design_title/caption has token-jaccard >= 0.5 with the story's best caption
        cutoff = now_utc - timedelta(hours=48)
        recent_jobs = (
            db.query(PublishJob)
            .join(Post)
            .filter(
                PublishJob.fanpage_id == d.fanpage_id,
                PublishJob.created_at >= cutoff
            )
            .all()
        )

        member_shortcodes = {m.shortcode for m in story.posts}
        best_caption_ts = token_set(best_post.caption or "")
        
        is_dup = False
        for job in recent_jobs:
            if any(sc in (job.post.ig_post_url or "") for sc in member_shortcodes):
                is_dup = True
                break
            
            job_caption_ts = token_set(job.ai_generated_caption or "")
            job_title_ts = token_set(job.design_title or "")
            if (best_caption_ts and job_caption_ts and jaccard(best_caption_ts, job_caption_ts) >= 0.5) or \
               (best_caption_ts and job_title_ts and jaccard(best_caption_ts, job_title_ts) >= 0.5):
                is_dup = True
                break

        if is_dup:
            d.status = DecisionStatus.SKIPPED_DEDUP
            d.reason = "duplicate post found"
            db.commit()
            continue

        # Action
        if d.via_viral_only_link:
            # via_viral_only_link decisions
            # find the existing Post by member shortcode in ig_post_url
            post = None
            link = None
            member_usernames = {m.ig_username for m in story.posts}
            viral_only_links = db.query(FanpageSource).join(IGSource).filter(
                FanpageSource.fanpage_id == d.fanpage_id,
                FanpageSource.trigger == "viral_only",
                IGSource.ig_username.in_(member_usernames)
            ).all()
            
            for l in viral_only_links:
                shortcodes = {m.shortcode for m in story.posts if m.ig_username == l.ig_source.ig_username}
                for sc in shortcodes:
                    post = db.query(Post).filter(
                        Post.ig_source_id == l.ig_source_id,
                        Post.ig_post_url.like(f"%{sc}%")
                    ).first()
                    if post:
                        link = l
                        break
                if post:
                    break
            
            if post and link:
                c = 0 if d.rule in ('fast', 'burst') else _fanpage_stagger(slot)
                if d.rule in ('fast', 'burst'):
                    c = 0 if slot == 0 else random.randint(0, news_stagger_max * 60)
                else:
                    c = _fanpage_stagger(slot)
                
                if fanout_link(db, post, link, countdown=c, radar_decision_id=d.id):
                    if d.status != DecisionStatus.CREATED:
                        d.status = DecisionStatus.DISPATCHED
                    d.post_id = post.id
                    db.commit()
                    slot += 1
            else:
                d.status = DecisionStatus.FAILED
                d.reason = "Post missing for viral_only link"
                db.commit()
        else:
            # niche (radar) decisions
            ig_source = db.query(IGSource).filter_by(ig_username=best_post.ig_username).first()
            if not ig_source:
                ig_source = IGSource(ig_username=best_post.ig_username, is_active=False)
                db.add(ig_source)
                db.flush()
            
            post = db.query(Post).filter_by(ig_post_url=f"https://www.instagram.com/p/{best_post.shortcode}/").first()
            if not post:
                post = Post(
                    ig_source_id=ig_source.id,
                    ig_media_id=f"radar:{best_post.shortcode}",
                    ig_post_url=f"https://www.instagram.com/p/{best_post.shortcode}/",
                    media_type=best_post.media_type,
                    original_caption=best_post.caption,
                    taken_at=best_post.taken_at,
                    image_source_urls=best_post.image_source_urls,
                    status=PostStatus.stored
                )
                db.add(post)
                db.flush()
                
                if post.image_source_urls:
                    from pathlib import Path
                    
                    post_dir = Path(app_settings.storage_base_path) / "posts" / str(post.uuid)
                    post_dir.mkdir(parents=True, exist_ok=True)
                    
                    local_paths = []
                    public_urls = []
                    
                    for idx, url in enumerate(post.image_source_urls):
                        filename = f"{idx}.jpg"
                        local_path = post_dir / filename
                        try:
                            download_ig_image(url, local_path)
                            local_paths.append(str(local_path))
                            public_urls.append(f"{app_settings.storage_base_url.rstrip('/')}/posts/{post.uuid}/{filename}")
                        except Exception as e:
                            logger.error("Failed to download image %s for radar post %d: %s", url, post.id, e)
                            
                    post.image_local_paths = local_paths
                    post.image_public_urls = public_urls
                    db.flush()
            
            c = 0
            if d.rule in ('fast', 'burst'):
                c = 0 if slot == 0 else random.randint(0, news_stagger_max * 60)
            else:
                c = _fanpage_stagger(slot)
                
            recreate_post_for_fanpage.apply_async(args=[post.id, d.fanpage_id], kwargs={"radar_decision_id": d.id}, countdown=c)
            
            d.status = DecisionStatus.DISPATCHED
            d.post_id = post.id
            db.commit()
            slot += 1

def _fanpage_stagger(slot: int) -> int:
    from app.tasks.fan_out import _FANPAGE_STAGGER_MIN, _FANPAGE_STAGGER_MAX
    return 0 if slot == 0 else random.randint(_FANPAGE_STAGGER_MIN, _FANPAGE_STAGGER_MAX)

