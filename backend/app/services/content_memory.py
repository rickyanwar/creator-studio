from datetime import datetime, timezone, timedelta
import json
from sqlalchemy.orm import Session

from app.models.target_fanpages import TargetFanpage
from app.models.strategy import FanpageContentMemory
from app.services import analytics

def build_auto_memory(db: Session, fanpage: TargetFanpage) -> dict:
    now = datetime.now(timezone.utc)
    since = now - timedelta(days=90)
    jobs = analytics.load_jobs_for_fanpages(db, [fanpage.id], since, now)
    
    has_metrics = False
    for j in jobs:
        if j.eng_final is not None:
            has_metrics = True
            break

    auto = {
        "timezone": fanpage.timezone,
        "computed_at": now.isoformat()
    }

    if has_metrics:
        detail = analytics.aggregate_fanpage_detail(fanpage, jobs, now, since, now)
        
        hours = [h for h in detail["by_hour"] if h["n"] >= 5 and h["eng_median"] is not None]
        hours.sort(key=lambda x: x["eng_median"], reverse=True)
        auto["best_hours"] = [h["hour"] for h in hours[:3]]
        
        weekdays = [w for w in detail["by_weekday"] if w["n"] >= 5 and w["eng_median"] is not None]
        weekdays.sort(key=lambda x: x["eng_median"], reverse=True)
        auto["best_weekdays"] = [w["weekday"] for w in weekdays[:3]]
        
        cts = [ct for ct in detail["by_content_type"] if ct["eng_median"] is not None]
        auto["content_types"] = [
            {"content_type": ct["content_type"], "eng_median": ct["eng_median"]} 
            for ct in cts
        ]
        
        top_posts = detail["top_posts"][:5]
        auto["top_posts"] = [
            {"title": p["title"], "engagement": p["engagement"]} 
            for p in top_posts
        ]
        
        auto["trend"] = detail["trend"]
        
    return auto

def update_auto(db: Session, fanpage_id: int, auto_data: dict) -> FanpageContentMemory:
    mem = db.query(FanpageContentMemory).filter_by(fanpage_id=fanpage_id).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not mem:
        mem = FanpageContentMemory(
            fanpage_id=fanpage_id,
            auto=auto_data,
            auto_updated_at=now
        )
        db.add(mem)
    else:
        mem.auto = auto_data
        mem.auto_updated_at = now
    db.commit()
    return mem

def update_notes(db: Session, fanpage_id: int, notes_data: dict) -> FanpageContentMemory:
    if len(json.dumps(notes_data)) > 16384:
        raise ValueError("Notes exceed 16 KB limit")
        
    mem = db.query(FanpageContentMemory).filter_by(fanpage_id=fanpage_id).first()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if not mem:
        mem = FanpageContentMemory(
            fanpage_id=fanpage_id,
            notes=notes_data,
            notes_updated_at=now
        )
        db.add(mem)
    else:
        mem.notes = notes_data
        mem.notes_updated_at = now
    db.commit()
    return mem

def get_memory(db: Session, fanpage_id: int) -> FanpageContentMemory | None:
    return db.query(FanpageContentMemory).filter_by(fanpage_id=fanpage_id).first()
