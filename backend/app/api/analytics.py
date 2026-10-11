from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Query, HTTPException
from sqlalchemy import func

from app.api.deps import CurrentUser, DB
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.services.analytics import load_jobs_for_fanpages, aggregate_fanpage_overview, aggregate_fanpage_detail

router = APIRouter(prefix="/analytics", tags=["analytics"])

def _get_metrics_block(db: DB) -> dict:
    settings = db.query(Settings).first()
    if not settings:
        return {
            "enabled": False,
            "plan_status": None,
            "checked_at": None,
            "last_error": None,
            "snapshots_total": 0
        }
    
    total = db.query(func.count(PostMetricSnapshot.id)).scalar()
    
    return {
        "enabled": settings.metrics_ingestion_enabled,
        "plan_status": settings.metrics_plan_status,
        "checked_at": settings.metrics_plan_checked_at.isoformat() if settings.metrics_plan_checked_at else None,
        "last_error": settings.metrics_last_error,
        "snapshots_total": total
    }

@router.get("/overview")
def get_overview(db: DB, _: CurrentUser, days: int = Query(28, ge=7, le=180)):
    now = datetime.now(timezone.utc)
    until = now
    since = now - timedelta(days=days)
    
    trend_since = now - timedelta(days=28)
    load_since = min(since, trend_since)
    
    fanpages = db.query(TargetFanpage).all()
    fanpage_ids = [f.id for f in fanpages]
    
    jobs = load_jobs_for_fanpages(db, fanpage_ids, load_since, until)
    metrics_block = _get_metrics_block(db)
    
    fanpage_stats = []
    for f in fanpages:
        fanpage_stats.append(aggregate_fanpage_overview(f, jobs, now, since, until))
        
    return {
        "days": days,
        "metrics": metrics_block,
        "fanpages": fanpage_stats
    }

@router.get("/fanpages/{fanpage_id}")
def get_fanpage_analytics(fanpage_id: int, db: DB, _: CurrentUser, days: int = Query(28, ge=7, le=180)):
    fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == fanpage_id).first()
    if not fanpage:
        raise HTTPException(status_code=404, detail="Fanpage not found")
        
    now = datetime.now(timezone.utc)
    until = now
    since = now - timedelta(days=days)
    
    trend_since = now - timedelta(days=28)
    load_since = min(since, trend_since)
    
    jobs = load_jobs_for_fanpages(db, [fanpage.id], load_since, until)
    metrics_block = _get_metrics_block(db)
    
    res = aggregate_fanpage_detail(fanpage, jobs, now, since, until)
    res["days"] = days
    res["metrics"] = metrics_block
    return res
