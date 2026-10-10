from fastapi import APIRouter

router = APIRouter(prefix="/hermes", tags=["hermes"])

from typing import Optional
from datetime import datetime
from fastapi import Depends, HTTPException
from pydantic import BaseModel

from app.api.deps import DB, require_hermes_scope
from app.models.target_fanpages import TargetFanpage
from app.services import content_memory

class MemoryResponse(BaseModel):
    fanpage_id: int
    auto: Optional[dict] = None
    notes: Optional[dict] = None
    auto_updated_at: Optional[datetime] = None
    notes_updated_at: Optional[datetime] = None

class MemoryWriteRequest(BaseModel):
    notes: dict

@router.get("/memory/{fanpage_id}", response_model=MemoryResponse)
def get_memory_hermes(fanpage_id: int, db: DB, token=Depends(require_hermes_scope("read"))):
    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    mem = content_memory.get_memory(db, fanpage_id)
    if not mem:
        return {"fanpage_id": fanpage_id}
    return {
        "fanpage_id": mem.fanpage_id,
        "auto": mem.auto,
        "notes": mem.notes,
        "auto_updated_at": mem.auto_updated_at,
        "notes_updated_at": mem.notes_updated_at,
    }

@router.put("/memory/{fanpage_id}", response_model=MemoryResponse)
def update_memory_hermes(fanpage_id: int, req: MemoryWriteRequest, db: DB, token=Depends(require_hermes_scope("memory:write"))):
    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    try:
        mem = content_memory.update_notes(db, fanpage_id, req.notes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {
        "fanpage_id": mem.fanpage_id,
        "auto": mem.auto,
        "notes": mem.notes,
        "auto_updated_at": mem.auto_updated_at,
        "notes_updated_at": mem.notes_updated_at,
    }

from typing import List
from fastapi import Query
from app.api.analytics import get_fanpage_analytics as internal_get_fanpage_analytics
from app.services import strategy
from app.models.strategy import StrategyRecommendation
from app.models.api_tokens import ApiToken

class FanpageHermesResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    timezone: str
    target_country: Optional[str]
    publish_sleep_start_hour: Optional[int]
    publish_sleep_end_hour: Optional[int]
    publish_daily_limit: int
    mode2_gallery_niches: List[str]
    caption_language: str

@router.get("/fanpages", response_model=List[FanpageHermesResponse])
def get_fanpages_hermes(db: DB, token: ApiToken = Depends(require_hermes_scope("read"))):
    fanpages = db.query(TargetFanpage).all()
    res = []
    for f in fanpages:
        res.append({
            "id": f.id,
            "name": f.name,
            "is_active": f.is_active,
            "timezone": f.timezone,
            "target_country": f.target_country,
            "publish_sleep_start_hour": f.publish_sleep_start_hour,
            "publish_sleep_end_hour": f.publish_sleep_end_hour,
            "publish_daily_limit": f.publish_daily_limit,
            "mode2_gallery_niches": f.mode2_gallery_niches,
            "caption_language": f.caption_language
        })
    return res

@router.get("/analytics/fanpages/{fanpage_id}")
def get_fanpage_analytics_hermes(fanpage_id: int, db: DB, token: ApiToken = Depends(require_hermes_scope("read")), days: int = Query(28, ge=7, le=180)):
    return internal_get_fanpage_analytics(fanpage_id, db, None, days)

class RecommendationCreateRequest(BaseModel):
    fanpage_id: int
    kind: str
    title: str
    proposal: dict
    rationale: str
    evidence: Optional[dict] = None

class RecommendationHermesResponse(BaseModel):
    id: int
    fanpage_id: int
    kind: str
    title: str
    proposal: dict
    rationale: str
    evidence: Optional[dict]
    status: str
    source: str
    created_at: datetime
    decided_at: Optional[datetime]
    decided_by: Optional[str]
    applied_at: Optional[datetime]
    apply_error: Optional[str]
    previous_values: Optional[dict]

@router.post("/recommendations", response_model=RecommendationHermesResponse, status_code=201)
def create_recommendation_hermes(req: RecommendationCreateRequest, db: DB, token: ApiToken = Depends(require_hermes_scope("recommendations:write"))):
    fp = db.query(TargetFanpage).filter_by(id=req.fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
        
    try:
        rec = strategy.create_recommendation(
            db=db,
            fanpage_id=req.fanpage_id,
            kind=req.kind,
            title=req.title,
            proposal=req.proposal,
            rationale=req.rationale,
            evidence=req.evidence,
            token_id=token.id
        )
    except strategy.LimitExceededError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
        
    return rec

@router.get("/recommendations", response_model=List[RecommendationHermesResponse])
def list_recommendations_hermes(db: DB, token: ApiToken = Depends(require_hermes_scope("read")), fanpage_id: Optional[int] = None, status: Optional[str] = None):
    q = db.query(StrategyRecommendation)
    if fanpage_id:
        q = q.filter(StrategyRecommendation.fanpage_id == fanpage_id)
    if status:
        q = q.filter(StrategyRecommendation.status == status)
        
    return q.order_by(StrategyRecommendation.created_at.desc()).all()
