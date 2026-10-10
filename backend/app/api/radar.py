from fastapi import APIRouter, HTTPException, status, Query
from sqlalchemy import func
from sqlalchemy.orm import joinedload
from typing import Optional
from datetime import datetime, timedelta, timezone

from app.api.deps import CurrentUser, DB
from app.models.radar import RadarAccount, RadarStory, RadarStoryDecision
from app.models.target_fanpages import TargetFanpage
from app.schemas.radar import RadarAccountCreate, RadarAccountUpdate, RadarAccountOut, RadarStoryOut

router = APIRouter(prefix="/api/radar", tags=["radar"])

@router.get("/accounts", response_model=list[RadarAccountOut])
def list_accounts(db: DB, _: CurrentUser, niche: Optional[str] = None):
    q = db.query(RadarAccount)
    if niche:
        q = q.filter(RadarAccount.niche == niche)
    return q.order_by(RadarAccount.niche, RadarAccount.ig_username).all()

@router.post("/accounts", response_model=RadarAccountOut, status_code=status.HTTP_201_CREATED)
def create_account(body: RadarAccountCreate, db: DB, _: CurrentUser):
    ig_username = body.ig_username.lower().lstrip('@')
    
    exists = db.query(RadarAccount).filter(
        func.lower(RadarAccount.niche) == body.niche.lower(),
        RadarAccount.ig_username == ig_username
    ).first()
    if exists:
        raise HTTPException(status_code=409, detail="Account already exists in this niche")
        
    acc = RadarAccount(
        niche=body.niche,
        ig_username=ig_username
    )
    db.add(acc)
    db.commit()
    db.refresh(acc)
    return acc

@router.patch("/accounts/{account_id}", response_model=RadarAccountOut)
def update_account(account_id: int, body: RadarAccountUpdate, db: DB, _: CurrentUser):
    acc = db.query(RadarAccount).filter(RadarAccount.id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
        
    if body.niche is not None:
        exists = db.query(RadarAccount).filter(
            func.lower(RadarAccount.niche) == body.niche.lower(),
            RadarAccount.ig_username == acc.ig_username,
            RadarAccount.id != account_id
        ).first()
        if exists:
            raise HTTPException(status_code=409, detail="Account already exists in that niche")
        acc.niche = body.niche
        
    if body.is_active is not None:
        acc.is_active = body.is_active
        
    db.commit()
    db.refresh(acc)
    return acc

@router.delete("/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: int, db: DB, _: CurrentUser):
    acc = db.query(RadarAccount).filter(RadarAccount.id == account_id).first()
    if not acc:
        raise HTTPException(status_code=404, detail="Account not found")
    db.delete(acc)
    db.commit()
    return None

@router.get("/niches", response_model=list[str])
def list_niches(db: DB, _: CurrentUser):
    # Distinct niches from radar_accounts
    acc_niches = [r[0] for r in db.query(RadarAccount.niche).distinct().all()]
    
    # Distinct from target_fanpages.radar_niches
    # radar_niches is an ARRAY(String)
    fp_niches_raw = db.query(TargetFanpage.radar_niches).all()
    fp_niches = []
    for (arr,) in fp_niches_raw:
        if arr:
            fp_niches.extend(arr)
            
    # And maybe gallery niches
    gal_niches_raw = db.query(TargetFanpage.mode2_gallery_niches).all()
    for (arr,) in gal_niches_raw:
        if arr:
            fp_niches.extend(arr)
            
    all_niches = set(acc_niches + fp_niches)
    return sorted(list(all_niches))

@router.get("/stories", response_model=list[RadarStoryOut])
def list_stories(
    db: DB, 
    _: CurrentUser, 
    niche: Optional[str] = None, 
    status: Optional[str] = None, 
    limit: int = Query(50, ge=1, le=200)
):
    q = db.query(RadarStory).options(
        joinedload(RadarStory.posts),
        joinedload(RadarStory.decisions).joinedload(RadarStoryDecision.fanpage)
    )
    if niche:
        q = q.filter(RadarStory.niche == niche)
    if status:
        q = q.filter(RadarStory.status == status)
        
    stories = q.order_by(RadarStory.first_seen_at.desc()).limit(limit).all()
    
    result = []
    for s in stories:
        members = []
        for p in s.posts:
            members.append({
                "ig_username": p.ig_username,
                "shortcode": p.shortcode,
                "post_url": f"https://www.instagram.com/p/{p.shortcode}/",
                "taken_at": p.taken_at,
                "latest_like_count": p.latest_like_count,
                "latest_comment_count": p.latest_comment_count,
                "thumbnail_url": p.thumbnail_url
            })
            
        decisions = []
        for d in s.decisions:
            decisions.append({
                "id": d.id,
                "fanpage_id": d.fanpage_id,
                "fanpage_name": d.fanpage.name if d.fanpage else f"Fanpage {d.fanpage_id}",
                "rule": d.rule,
                "status": d.status,
                "shadow": d.shadow,
                "would_trigger_at": d.would_trigger_at,
                "reason": d.reason,
                "publish_job_id": d.publish_job_id,
                "preview_image_path": d.preview_image_path,
                "preview_status": getattr(d, 'preview_status', None),
                "preview_error": getattr(d, 'preview_error', None)
            })
            
        result.append({
            "id": s.id,
            "niche": s.niche,
            "first_seen_at": s.first_seen_at,
            "last_member_at": s.last_member_at,
            "member_count": s.member_count,
            "distinct_accounts": s.distinct_accounts,
            "heat_score": s.heat_score,
            "status": s.status,
            "shelf_kind": s.shelf_kind,
            "expires_at": s.expires_at,
            "final_max_likes_24h": s.final_max_likes_24h,
            "members": members,
            "decisions": decisions
        })
        
    return result

@router.get("/shadow-report")
def get_shadow_report(db: DB, _: CurrentUser, days: int = Query(7, ge=1, le=30)):
    from app.services.radar_engine import shadow_report, StoryView, DecisionRecord, FanpageRadarConfig
    from app.models.radar import RadarStoryDecision
    
    since = datetime.now(timezone.utc) - timedelta(days=days)
    since_naive = since.replace(tzinfo=None)
    
    stories_raw = db.query(RadarStory).filter(RadarStory.first_seen_at >= since_naive).all()
    stories = [
        StoryView(
            id=s.id, niche=s.niche, first_seen_at=s.first_seen_at, last_member_at=s.last_member_at,
            member_count=s.member_count, distinct_accounts=s.distinct_accounts, best_post_id=s.best_post_id,
            content_hint=s.content_hint, shelf_kind=s.shelf_kind, expires_at=s.expires_at,
            status=s.status, heat_score=s.heat_score, final_max_likes_24h=s.final_max_likes_24h
        ) for s in stories_raw
    ]
    
    decisions_raw = db.query(RadarStoryDecision).options(
        joinedload(RadarStoryDecision.story).joinedload(RadarStory.best_post)
    ).filter(
        RadarStoryDecision.story.has(RadarStory.first_seen_at >= since_naive)
    ).all()
    
    decisions = []
    for d in decisions_raw:
        bp = d.story.best_post if d.story else None
        minutes = (d.would_trigger_at - d.story.first_seen_at).total_seconds() / 60 if d.would_trigger_at and d.story and d.story.first_seen_at else -1.0
        dr = DecisionRecord(
            story_id=d.story_id,
            fanpage_id=d.fanpage_id,
            rule=d.rule,
            minutes_to_trigger=minutes,
            best_ig_username=bp.ig_username if bp else "",
            shortcode=bp.shortcode if bp else "",
            final_likes=bp.latest_like_count if bp else None
        )
        decisions.append(dr)
        
    cfgs_raw = db.query(TargetFanpage).filter(TargetFanpage.is_active == True, TargetFanpage.radar_enabled == True).all()
    cfgs = []
    for c in cfgs_raw:
        cfgs.append(FanpageRadarConfig(
            fanpage_id=c.id,
            radar_enabled=c.radar_enabled,
            radar_shadow=c.radar_shadow,
            radar_niches=set(c.radar_niches or []),
            min_likes=c.radar_min_likes,
            confirm_ratio=c.radar_confirm_ratio,
            fast_ratio=c.radar_fast_ratio,
            fast_window_min=c.radar_fast_window_min,
            burst_enabled=c.radar_burst_enabled,
            shelf_news_h=c.radar_shelf_news_h,
            shelf_evergreen_h=c.radar_shelf_evergreen_h,
            viral_only_source_ids=set()
        ))
        
    return shadow_report(stories, decisions, cfgs)

@router.post("/decisions/{decision_id}/preview", status_code=status.HTTP_202_ACCEPTED)
def preview_decision_redesign(decision_id: int, db: DB, _: CurrentUser):
    from app.models.radar import RadarStoryDecision
    from app.tasks.ig_recreate import run_preview_redesign
    
    decision = db.query(RadarStoryDecision).filter_by(id=decision_id).first()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
        
    if decision.preview_status in ('queued', 'running'):
        raise HTTPException(status_code=409, detail="Redesign is already in progress")
        
    decision.preview_status = 'queued'
    decision.preview_error = None
    db.commit()
    
    run_preview_redesign.apply_async((decision.id,), queue='visual')
        
    return {"status": "queued"}

@router.post("/decisions/{decision_id}/regenerate", status_code=status.HTTP_202_ACCEPTED)
def regenerate_decision_redesign(decision_id: int, db: DB, _: CurrentUser):
    from app.models.radar import RadarStoryDecision
    from app.tasks.ig_recreate import render_ig_recreate, run_preview_redesign
    
    decision = db.query(RadarStoryDecision).filter_by(id=decision_id).first()
    if not decision:
        raise HTTPException(status_code=404, detail="Decision not found")
        
    if decision.preview_status in ('queued', 'running'):
        raise HTTPException(status_code=409, detail="Redesign is already in progress")
        
    decision.preview_status = 'queued'
    decision.preview_error = None
    db.commit()
    
    if not decision.publish_job_id:
        run_preview_redesign.apply_async((decision.id,), queue='visual')
    else:
        render_ig_recreate.apply_async((decision.publish_job_id,), queue='visual')
        
    return {"status": "queued"}
