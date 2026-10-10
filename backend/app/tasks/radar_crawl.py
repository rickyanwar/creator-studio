import logging
import time
import uuid
import httpx
from datetime import datetime, timezone, timedelta
from typing import Optional

import redis

from app.tasks.celery_app import celery_app
from app.database import SessionLocal
from app.config import get_settings
from app.tasks.radar_locks import _RELEASE_LUA

logger = logging.getLogger(__name__)
settings = get_settings()

def _redis():
    return redis.from_url(settings.redis_url, decode_responses=True)

@celery_app.task(name="app.tasks.radar_crawl.radar_crawl_tick", bind=True, max_retries=0)
def radar_crawl_tick(self):
    from app.models.settings import Settings as DBSettings
    
    r = _redis()
    lock_key = "radar:tick_running"
    token = str(uuid.uuid4())
    if not r.set(lock_key, token, nx=True, ex=900):
        logger.info("radar_crawl_tick already running, skipping")
        return

    db = SessionLocal()
    
    try:
        try:
            # Skip if mode 1 crawler wave is running
            from app.tasks.crawler import WAVE_KEY
            if r.get(WAVE_KEY):
                return
                
            db_settings = db.query(DBSettings).filter_by(id=1).first()
            sleep_start = db_settings.radar_sleep_start_wib if db_settings and db_settings.radar_sleep_start_wib is not None else settings.radar_sleep_start_wib
            sleep_end = db_settings.radar_sleep_end_wib if db_settings and db_settings.radar_sleep_end_wib is not None else settings.radar_sleep_end_wib
            
            # Check sleep window
            if sleep_start is not None and sleep_end is not None:
                import pytz
                wib = pytz.timezone("Asia/Jakarta")
                now_wib = datetime.now(wib)
                hour = now_wib.hour
                
                if sleep_start < sleep_end:
                    if sleep_start <= hour < sleep_end:
                        return
                else: # wraps midnight
                    if hour >= sleep_start or hour < sleep_end:
                        return
                        
            _process_radar_tick(db, r, db_settings)
        finally:
            pass # handled by lock release
    finally:
        db.close()
        r.eval(_RELEASE_LUA, 1, lock_key, token)

def _process_radar_tick(db, r, db_settings):
    from app.models.radar import RadarAccount
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource
    from app.models.target_fanpages import TargetFanpage
    from app.models.radar import RadarPost
    from sqlalchemy import exists, or_, func
    from app.services.ig_viewer_scraper import fetch_many_recent_posts, ViewerBusyError
    from app.services.radar_ingest import ingest_medias
    
    from app.services.radar_time import utcnow_naive
    
    now = utcnow_naive()
    
    # 1. Which usernames the radar tracks
    # active radar_accounts
    radar_accs = db.query(RadarAccount).filter(RadarAccount.is_active == True).all()
    tracked = {acc.ig_username.lower(): {'radar_acc': acc, 'ig_source': None} for acc in radar_accs}
    
    # ig_sources with active fanpage_sources link (trigger == 'viral_only') to active fanpage
    ig_sources = db.query(IGSource).filter(
        IGSource.is_active == True,
        exists().where(
            FanpageSource.ig_source_id == IGSource.id,
            FanpageSource.is_active == True,
            FanpageSource.trigger == 'viral_only',
            FanpageSource.fanpage_id == TargetFanpage.id,
            TargetFanpage.is_active == True
        )
    ).all()
    
    for src in ig_sources:
        uname = src.ig_username.lower()
        if uname not in tracked:
            tracked[uname] = {'radar_acc': None, 'ig_source': src}
        else:
            tracked[uname]['ig_source'] = src
            
    if not tracked:
        return
        
    # Get settings
    v_hot_int = db_settings.radar_very_hot_interval_min if db_settings and db_settings.radar_very_hot_interval_min else 8
    hot_win = db_settings.radar_hot_window_h if db_settings and db_settings.radar_hot_window_h else 24
    hot_int = db_settings.radar_hot_interval_min if db_settings and db_settings.radar_hot_interval_min else 12
    cold_int = db_settings.radar_cold_interval_min if db_settings and db_settings.radar_cold_interval_min else 60
    
    candidates = []
    
    for uname, data in tracked.items():
        radar_acc = data['radar_acc']
        ig_source = data['ig_source']
        original_uname = radar_acc.ig_username if radar_acc else ig_source.ig_username
        
        last_checked_str = r.hget("radar:last_checked", uname)
        last_checked = datetime.fromisoformat(last_checked_str).replace(tzinfo=None) if last_checked_str else datetime.min
        
        elapsed_min = (now - last_checked).total_seconds() / 60
        
        # Determine hotness
        latest_post = db.query(RadarPost).filter(
            RadarPost.ig_username.ilike(uname)
        ).order_by(RadarPost.taken_at.desc()).first()
        
        interval = cold_int
        leader_score = radar_acc.leader_score if radar_acc else 0.0
        
        if latest_post and latest_post.taken_at:
            post_age_h = (now - latest_post.taken_at).total_seconds() / 3600
            
            if post_age_h <= 1.0:
                interval = v_hot_int
                hotness = 2
            elif post_age_h <= hot_win:
                interval = hot_int
                hotness = 1
            else:
                interval = max(10, cold_int * (1 - 0.5 * leader_score))
                hotness = 0
        else:
            interval = max(10, cold_int * (1 - 0.5 * leader_score))
            hotness = 0
            
        if elapsed_min >= interval:
            overdue = elapsed_min - interval
            candidates.append((hotness, overdue, uname, original_uname, data))
            
    if not candidates:
        return
        
    # Sort by very hot > hot > cold, then by most overdue
    candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
    to_fetch = candidates[:5]
    
    usernames_to_fetch = [c[3] for c in to_fetch]
    
    start_time = time.time()
    
    try:
        results = fetch_many_recent_posts(usernames_to_fetch, amount=12)
    except ViewerBusyError as e:
        logger.info("Radar viewer busy: %s", e)
        return
        
    batch_duration = time.time() - start_time
    avg_duration = batch_duration / len(usernames_to_fetch) if usernames_to_fetch else 0
        
    for _, _, uname, original_uname, data in to_fetch:
        r.hset("radar:last_checked", uname, utcnow_naive().isoformat())
        
        res = results.get(original_uname)
        radar_acc = data['radar_acc']
        ig_source = data['ig_source']
        
        if isinstance(res, Exception):
            logger.warning("Radar fetch error for %s: %s", original_uname, res)
            if radar_acc:
                radar_acc.last_error = str(res)[:255]
                db.commit()
            continue
            
        if not res:
            continue
            
        if radar_acc:
            radar_acc.last_error = None
            if radar_acc.avg_scrape_seconds:
                radar_acc.avg_scrape_seconds = 0.3 * avg_duration + 0.7 * radar_acc.avg_scrape_seconds
            else:
                radar_acc.avg_scrape_seconds = avg_duration
                
        try:
            from app.services.radar_ingest import default_thumb_fetcher
            ingest_medias(
                db,
                username=original_uname,
                radar_account_id=radar_acc.id if radar_acc else None,
                ig_source_id=ig_source.id if ig_source else None,
                medias=res,
                now=now,
                thumb_fetcher=default_thumb_fetcher
            )
        except Exception as e:
            logger.error("Error ingesting radar medias for %s: %s", original_uname, e, exc_info=True)
            
        # Scrape once, feed both: Also ingest into Mode 1 if tracked
        if ig_source:
            # check if it's an active Mode 1 source
            is_mode1_tracked = db.query(
                exists().where(
                    FanpageSource.ig_source_id == ig_source.id,
                    FanpageSource.is_active == True,
                    FanpageSource.fanpage_id == TargetFanpage.id,
                    TargetFanpage.is_active == True,
                    TargetFanpage.mode1_ig_repost_enabled == True
                )
            ).scalar()
            
            if is_mode1_tracked:
                from app.tasks.crawler import _ingest_mode1_medias
                try:
                    _ingest_mode1_medias(db, ig_source, res)
                except Exception as e:
                    logger.error("Error cross-ingesting to Mode 1 for %s: %s", original_uname, e, exc_info=True)
