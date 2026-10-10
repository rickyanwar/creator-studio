from typing import List, Set, Tuple, Dict
from datetime import datetime
from sqlalchemy.orm import Session, selectinload
from sqlalchemy import select, and_
from app.models.radar import RadarStory, RadarPost, RadarSnapshot, RadarAccount, RadarStoryDecision
from app.models.target_fanpages import TargetFanpage
from app.models.fanpage_sources import FanpageSource
from app.services.radar_engine import (
    PostView, SnapshotView, StoryView, FanpageRadarConfig,
    ClusterPlan, EvaluationPlan, NightlyPlan
)

def load_new_posts(db: Session, since: datetime) -> List[PostView]:
    q = select(RadarPost).where(
        RadarPost.story_id == None, RadarPost.first_seen_at >= since
    ).options(
        selectinload(RadarPost.snapshots),
        selectinload(RadarPost.account)
    )
    posts = db.execute(q).scalars().all()
    
    views = []
    for p in posts:
        # Load snapshots (not deeply, just simple fields)
        snaps = []
        for s in p.snapshots:
            snaps.append(SnapshotView(s.observed_at, s.age_minutes, s.like_count))
            
        views.append(PostView(
            id=p.id,
            account_key=p.ig_username,
            niche=p.account.niche if p.account else None,
            ig_source_id=p.ig_source_id,
            shortcode=p.shortcode,
            taken_at=p.taken_at,
            caption=p.caption or "",
            phash=p.phash,
            latest_like_count=p.latest_like_count,
            image_count=len(p.image_source_urls) if p.image_source_urls else 0,
            snapshots=sorted(snaps, key=lambda s: s.age_minutes)
        ))
    return views

def load_recent_stories(db: Session, since: datetime) -> List[Tuple[StoryView, List[PostView]]]:
    q = select(RadarStory).where(and_(
        RadarStory.last_member_at >= since,
        RadarStory.status == "watching"
    )).options(
        selectinload(RadarStory.posts).selectinload(RadarPost.snapshots),
        selectinload(RadarStory.posts).selectinload(RadarPost.account)
    )
    stories = db.execute(q).scalars().all()
    
    results = []
    for s in stories:
        sv = StoryView(
            id=s.id,
            niche=s.niche,
            first_seen_at=s.first_seen_at,
            last_member_at=s.last_member_at,
            member_count=s.member_count,
            distinct_accounts=s.distinct_accounts,
            best_post_id=s.best_post_id,
            content_hint=s.content_hint,
            shelf_kind=s.shelf_kind,
            expires_at=s.expires_at,
            status=s.status,
            heat_score=s.heat_score,
            final_max_likes_24h=s.final_max_likes_24h
        )
        
        members = []
        for p in s.posts:
            snaps = []
            for snap in p.snapshots:
                snaps.append(SnapshotView(snap.observed_at, snap.age_minutes, snap.like_count))
            members.append(PostView(
                id=p.id,
                account_key=p.ig_username,
                niche=p.account.niche if p.account else None,
                ig_source_id=p.ig_source_id,
                shortcode=p.shortcode,
                taken_at=p.taken_at,
                caption=p.caption or "",
                phash=p.phash,
                latest_like_count=p.latest_like_count,
                image_count=len(p.image_source_urls) if p.image_source_urls else 0,
                snapshots=sorted(snaps, key=lambda s: s.age_minutes)
            ))
            
        results.append((sv, members))
        
    return results

def apply_cluster_plan(db: Session, plan: ClusterPlan):
    # Updates
    for up in plan.updates:
        s = db.query(RadarStory).filter(RadarStory.id == up.story_id).first()
        if s:
            s.member_count = up.member_count
            s.distinct_accounts = up.distinct_accounts
            s.last_member_at = up.last_member_at
            s.best_post_id = up.best_post_id
            
            db.query(RadarPost).filter(RadarPost.id.in_(up.assigned_post_ids)).update(
                {"story_id": s.id}, synchronize_session=False
            )
            
    # New stories
    for ns in plan.new_stories:
        s = RadarStory(
            niche=ns.niche,
            first_seen_at=ns.first_seen_at,
            last_member_at=ns.first_seen_at,
            member_count=1,
            distinct_accounts=1,
            best_post_id=ns.best_post_id,
        )
        db.add(s)
        db.flush()
        
        db.query(RadarPost).filter(RadarPost.id.in_(ns.assigned_post_ids)).update(
            {"story_id": s.id}, synchronize_session=False
        )

def load_fanpage_configs(db: Session) -> List[FanpageRadarConfig]:
    fanpages = db.execute(select(TargetFanpage).where(TargetFanpage.radar_enabled == True, TargetFanpage.is_active == True)).scalars().all()
    
    # Load viral only source ids
    sources = db.execute(select(FanpageSource).where(FanpageSource.trigger == 'viral_only')).scalars().all()
    viral_map = {}
    for src in sources:
        if src.fanpage_id not in viral_map:
            viral_map[src.fanpage_id] = set()
        viral_map[src.fanpage_id].add(src.ig_source_id)
        
    cfgs = []
    for fp in fanpages:
        cfgs.append(FanpageRadarConfig(
            fanpage_id=fp.id,
            radar_enabled=fp.radar_enabled,
            radar_shadow=fp.radar_shadow,
            radar_niches=set(fp.radar_niches or []),
            min_likes=fp.radar_min_likes,
            confirm_ratio=fp.radar_confirm_ratio,
            fast_ratio=fp.radar_fast_ratio,
            fast_window_min=fp.radar_fast_window_min,
            burst_enabled=fp.radar_burst_enabled,
            shelf_news_h=fp.radar_shelf_news_h,
            shelf_evergreen_h=fp.radar_shelf_evergreen_h,
            viral_only_source_ids=viral_map.get(fp.id, set())
        ))
    return cfgs

def load_account_histories(db: Session, account_keys: Set[str], limit: int = 20) -> Dict[str, List[PostView]]:
    # To optimize, we can load up to `limit` posts per account
    # In practice, for a simple implementation, we can run a window function or just load recent posts.
    
    # Simple approach for now
    q = select(RadarPost).where(RadarPost.ig_username.in_(account_keys)).order_by(RadarPost.taken_at.desc())
    all_posts = db.execute(q).scalars().all()
    
    histories = {}
    for p in all_posts:
        if p.ig_username not in histories:
            histories[p.ig_username] = []
            
        if len(histories[p.ig_username]) < limit:
            snaps = [SnapshotView(s.observed_at, s.age_minutes, s.like_count) for s in p.snapshots]
            histories[p.ig_username].append(PostView(
                id=p.id,
                account_key=p.ig_username,
                niche=p.account.niche if p.account else None,
                ig_source_id=p.ig_source_id,
                shortcode=p.shortcode,
                taken_at=p.taken_at,
                caption=p.caption or "",
                phash=p.phash,
                latest_like_count=p.latest_like_count,
                image_count=len(p.image_source_urls) if p.image_source_urls else 0,
                snapshots=sorted(snaps, key=lambda s: s.age_minutes)
            ))
            
    return histories

def load_existing_decisions(db: Session, story_ids: List[int]) -> Set[Tuple[int, int]]:
    q = select(RadarStoryDecision.story_id, RadarStoryDecision.fanpage_id).where(RadarStoryDecision.story_id.in_(story_ids))
    return set(db.execute(q).all())

def apply_evaluation_plan(db: Session, plan: EvaluationPlan):
    for u in plan.story_updates:
        s_id = u.pop('id')
        db.query(RadarStory).filter(RadarStory.id == s_id).update(u, synchronize_session=False)
        
    for d in plan.decisions:
        dec = RadarStoryDecision(
            story_id=d.story_id,
            fanpage_id=d.fanpage_id,
            rule=d.rule,
            reason=d.reason,
            shadow=d.shadow,
            status=d.status,
            via_viral_only_link=d.via_viral_only_link,
            post_id=None,
            publish_job_id=None,
            caption_text=None
        )
        db.add(dec)

def apply_nightly_plan(db: Session, plan: NightlyPlan):
    for acc_key, score in plan.leader_scores.items():
        db.query(RadarAccount).filter(RadarAccount.ig_username == acc_key).update({"leader_score": score}, synchronize_session=False)
        
    for s_id, max_likes in plan.final_likes.items():
        db.query(RadarStory).filter(RadarStory.id == s_id).update({"final_max_likes_24h": max_likes}, synchronize_session=False)
        
    db.query(RadarSnapshot).filter(RadarSnapshot.observed_at < plan.delete_snapshots_before).delete(synchronize_session=False)

