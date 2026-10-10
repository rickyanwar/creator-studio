import logging
import uuid
import redis
from datetime import timedelta
from typing import Optional
from celery import shared_task
from app.database import SessionLocal
from app.config import get_settings
from app.services.radar_time import utcnow_naive
from app.services.radar_repository import (
    load_new_posts, load_recent_stories, apply_cluster_plan,
    load_fanpage_configs, load_account_histories, load_existing_decisions,
    apply_evaluation_plan, apply_nightly_plan
)
from app.services.radar_engine import (
    cluster_posts, evaluate_all, nightly_plan
)
from app.tasks.radar_locks import _RELEASE_LUA

logger = logging.getLogger(__name__)

# Mock LLM helpers - in real system, would import and use 9Router wrapper
def mock_judge_same_story(a: str, b: str) -> bool:
    return False

def mock_classify_shelf(text: str) -> str:
    return "news"

def _redis():
    return redis.from_url(get_settings().redis_url, decode_responses=True)

@shared_task(name="app.tasks.radar.evaluate_radar")
def evaluate_radar():
    r = _redis()
    lock_key = "radar:evaluate_running"
    token = str(uuid.uuid4())
    if not r.set(lock_key, token, nx=True, ex=180):
        logger.info("evaluate_radar already running, skipping")
        return

    db = SessionLocal()
    try:
        now = utcnow_naive()
        # Cluster unassigned posts
        recent_since = now - timedelta(hours=48)
        new_posts = load_new_posts(db, since=now - timedelta(days=2))
        if new_posts:
            recent_stories = load_recent_stories(db, since=recent_since)
            
            c_plan = cluster_posts(new_posts, recent_stories, judge=mock_judge_same_story, now=now)
            apply_cluster_plan(db, c_plan)
            db.commit()
            
        # Re-load recent stories that are not stale or final
        recent_stories = load_recent_stories(db, since=recent_since)
        cfgs = load_fanpage_configs(db)
        
        if recent_stories and cfgs:
            # gather members for evaluation
            stories_to_evaluate = []
            story_members = {}
            account_keys = set()
            story_ids = []
            
            for sv, members in recent_stories:
                if sv.status in ("stale", "skipped", "triggered"):
                    continue
                stories_to_evaluate.append(sv)
                story_members[sv.id] = members
                story_ids.append(sv.id)
                for m in members:
                    account_keys.add(m.account_key)
                    
            if stories_to_evaluate:
                histories = load_account_histories(db, account_keys, limit=20)
                existing_decisions = load_existing_decisions(db, story_ids)
                
                eval_plan = evaluate_all(
                    stories_to_evaluate,
                    story_members,
                    histories,
                    cfgs,
                    now,
                    existing_decisions,
                    classify_shelf=mock_classify_shelf
                )
                
                apply_evaluation_plan(db, eval_plan)
                db.commit()
                
    except Exception as e:
        logger.error(f"Error in evaluate_radar: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()
        r.eval(_RELEASE_LUA, 1, lock_key, token)

@shared_task(name="app.tasks.radar.radar_nightly")
def radar_nightly():
    r = _redis()
    lock_key = "radar:nightly_running"
    token = str(uuid.uuid4())
    if not r.set(lock_key, token, nx=True, ex=3600):
        logger.info("radar_nightly already running, skipping")
        return

    db = SessionLocal()
    try:
        now = utcnow_naive()
        all_stories = load_recent_stories(db, since=now - timedelta(days=30))
        
        stories_view = []
        members_map = {}
        story_has_decision = set()
        
        # We need to know which stories have decisions
        # load all decision story_ids in last 30 days
        from app.models.radar import RadarStoryDecision
        from sqlalchemy import select
        q = select(RadarStoryDecision.story_id).distinct()
        decisions = db.execute(q).scalars().all()
        story_has_decision.update(decisions)
        
        for sv, members in all_stories:
            stories_view.append(sv)
            members_map[sv.id] = members
            
        n_plan = nightly_plan(stories_view, members_map, story_has_decision, now)
        apply_nightly_plan(db, n_plan)
        db.commit()
        
    except Exception as e:
        logger.error(f"Error in radar_nightly: {e}", exc_info=True)
        db.rollback()
    finally:
        db.close()
        r.eval(_RELEASE_LUA, 1, lock_key, token)
