import logging
import httpx
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Callable, Any, Optional

from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert

from app.models.radar import RadarPost, RadarSnapshot, RadarAccount
from app.services.radar_clustering import compute_phash
from app.services.radar_time import to_naive_utc
from app.tasks.image_saver import download_ig_image

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class IngestResult:
    posts_inserted: int
    posts_updated: int
    snapshots_inserted: int

def default_thumb_fetcher(url: str) -> bytes | None:
    return download_ig_image(url)

def ingest_medias(
    db: Session,
    *,
    username: str,
    radar_account_id: int | None,
    ig_source_id: int | None,
    medias: list[Any],
    now: datetime,
    thumb_fetcher: Callable[[str], bytes | None]
) -> IngestResult:
    """Ingest medias into Radar (radar_posts and radar_snapshots)."""
    from app.tasks.crawler import _extract_image_urls
    
    now = to_naive_utc(now)
    
    # Needs to match settings, default 48h
    from app.config import get_settings
    from app.models.settings import Settings as DBSettings
    
    settings = get_settings()
    db_settings = db.query(DBSettings).filter_by(id=1).first()
    max_age_h = db_settings.radar_track_max_age_h if db_settings and db_settings.radar_track_max_age_h else 48
    
    cutoff = now - timedelta(hours=max_age_h)
    
    posts_inserted = 0
    posts_updated = 0
    snapshots_inserted = 0
    
    shortcodes = [m.code for m in medias if hasattr(m, "code") and m.code]
    existing_posts = {
        p.shortcode: p 
        for p in db.query(RadarPost).filter(RadarPost.shortcode.in_(shortcodes)).all()
    }
    
    for media in medias:
        if media.media_type not in (1, 8):  # IMAGE or ALBUM
            continue
            
        taken_at = to_naive_utc(getattr(media, "taken_at", None))
        if not taken_at:
            continue
            
        if taken_at < cutoff:
            continue
            
        shortcode = media.code
        
        resources = getattr(media, "resources", []) or []
        images_in_post = [r for r in resources if getattr(r, "media_type", None) == 1]
        
        if media.media_type == 8:
            image_count = len(images_in_post) if images_in_post else len(resources)
            if image_count == 0:
                continue
            media_type = 'album'
        else:
            media_type = 'image'
            
        caption_raw = getattr(media, "caption_text", None) or ""
        if isinstance(caption_raw, dict):
            caption_raw = caption_raw.get("text", "") or ""
            
        all_urls = _extract_image_urls(media)
        thumb_url = getattr(media, "thumbnail_url", None) or getattr(media, "url", None)
        if not thumb_url and all_urls:
            thumb_url = all_urls[0]
            
        thumb_url = str(thumb_url) if thumb_url else None
        
        existing_post = existing_posts.get(shortcode)
        
        phash = None
        if existing_post and existing_post.phash:
            phash = existing_post.phash
        elif thumb_url:
            image_bytes = thumb_fetcher(thumb_url)
            if image_bytes:
                try:
                    phash = compute_phash(image_bytes)
                except Exception as e:
                    logger.debug("Failed to compute phash for %s: %s", shortcode, e)
                    
        like_count = getattr(media, "like_count", None)
        comment_count = getattr(media, "comment_count", None)
        
        stmt = insert(RadarPost).values(
            shortcode=shortcode,
            radar_account_id=radar_account_id,
            ig_source_id=ig_source_id,
            ig_username=username,
            taken_at=taken_at,
            caption=str(caption_raw),
            media_type=media_type,
            thumbnail_url=thumb_url,
            image_source_urls=all_urls,
            phash=phash,
            latest_like_count=like_count,
            latest_comment_count=comment_count,
            latest_observed_at=now
        )
        
        upsert_stmt = stmt.on_conflict_do_update(
            index_elements=['shortcode'],
            set_={
                'radar_account_id': stmt.excluded.radar_account_id,
                'ig_source_id': stmt.excluded.ig_source_id,
                'ig_username': stmt.excluded.ig_username,
                'caption': stmt.excluded.caption,
                'thumbnail_url': stmt.excluded.thumbnail_url,
                'image_source_urls': stmt.excluded.image_source_urls,
                'phash': stmt.excluded.phash,
                'latest_like_count': stmt.excluded.latest_like_count,
                'latest_comment_count': stmt.excluded.latest_comment_count,
                'latest_observed_at': stmt.excluded.latest_observed_at
            }
        )
        
        # Execute upsert
        res = db.execute(upsert_stmt)
        if res.rowcount > 0:
            # PostgreSQL on_conflict_do_update returns 1 for insert, 2 for update
            if getattr(res, 'returned_defaults', None) and res.returned_defaults:
                # Can't reliably tell from rowcount without RETURNING in some drivers,
                # but let's assume it was inserted or updated.
                pass
        
        db.flush()
        
        # Ensure we get the post.id for snapshot
        post_id = db.query(RadarPost.id).filter_by(shortcode=shortcode).scalar()
        
        if not existing_post:
            posts_inserted += 1
        else:
            posts_updated += 1
            
        age_minutes = int((now - taken_at).total_seconds() / 60)
        
        snapshot = RadarSnapshot(
            radar_post_id=post_id,
            observed_at=now,
            age_minutes=age_minutes,
            like_count=like_count,
            comment_count=comment_count
        )
        db.add(snapshot)
        snapshots_inserted += 1
        
    if radar_account_id:
        acc = db.query(RadarAccount).filter_by(id=radar_account_id).first()
        if acc:
            acc.last_checked_at = now
            if posts_inserted > 0 or posts_updated > 0:
                acc.last_post_seen_at = now
    
    db.commit()
    return IngestResult(posts_inserted=posts_inserted, posts_updated=posts_updated, snapshots_inserted=snapshots_inserted)
