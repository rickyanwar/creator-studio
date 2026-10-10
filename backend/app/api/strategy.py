from typing import List, Optional
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DB
from app.models.api_tokens import ApiToken
from app.services import api_tokens

router = APIRouter(prefix="/strategy", tags=["strategy"])

class TokenCreateRequest(BaseModel):
    name: str
    scopes: List[str]

class TokenResponse(BaseModel):
    id: int
    name: str
    scopes: List[str]
    created_at: datetime
    last_used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    
    class Config:
        from_attributes = True

class TokenCreateResponse(TokenResponse):
    token: str

@router.get("/tokens", response_model=List[TokenResponse])
def list_tokens(db: DB, user: CurrentUser):
    tokens = db.query(ApiToken).order_by(ApiToken.created_at.desc()).all()
    return tokens

@router.post("/tokens", response_model=TokenCreateResponse)
def create_token(req: TokenCreateRequest, db: DB, user: CurrentUser):
    try:
        db_token, plaintext = api_tokens.create_token(db, req.name, req.scopes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    
    return {
        "id": db_token.id,
        "name": db_token.name,
        "scopes": db_token.scopes,
        "created_at": db_token.created_at,
        "last_used_at": db_token.last_used_at,
        "revoked_at": db_token.revoked_at,
        "token": plaintext
    }

@router.delete("/tokens/{token_id}")
def revoke_token(token_id: int, db: DB, user: CurrentUser):
    token = db.query(ApiToken).filter(ApiToken.id == token_id).first()
    if not token:
        raise HTTPException(status_code=404, detail="Token not found")
    
    if not token.revoked_at:
        token.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        db.commit()
    
    return {"status": "ok"}

from app.services import content_memory
from app.models.target_fanpages import TargetFanpage

class MemoryResponse(BaseModel):
    fanpage_id: int
    auto: Optional[dict] = None
    notes: Optional[dict] = None
    auto_updated_at: Optional[datetime] = None
    notes_updated_at: Optional[datetime] = None

@router.get("/memory/{fanpage_id}", response_model=MemoryResponse)
def get_memory(fanpage_id: int, db: DB, user: CurrentUser):
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

@router.post("/memory/{fanpage_id}/refresh", response_model=MemoryResponse)
def refresh_memory(fanpage_id: int, db: DB, user: CurrentUser):
    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    auto_data = content_memory.build_auto_memory(db, fp)
    mem = content_memory.update_auto(db, fanpage_id, auto_data)
    return {
        "fanpage_id": mem.fanpage_id,
        "auto": mem.auto,
        "notes": mem.notes,
        "auto_updated_at": mem.auto_updated_at,
        "notes_updated_at": mem.notes_updated_at,
    }

from app.models.strategy import StrategyRecommendation
from app.models.target_fanpages import TargetFanpage
from app.services import strategy

class RecommendationResponse(BaseModel):
    id: int
    fanpage_id: int
    fanpage_name: str
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
    current_values: Optional[dict]

def _build_recommendation_response(rec: StrategyRecommendation, fanpage: TargetFanpage) -> dict:
    current_values = None
    if rec.kind in strategy.APPLICABLE_KINDS:
        current_values = {}
        for k in rec.proposal.keys():
            current_values[k] = getattr(fanpage, k, None)
            
    return {
        "id": rec.id,
        "fanpage_id": rec.fanpage_id,
        "fanpage_name": fanpage.name if fanpage else "",
        "kind": rec.kind,
        "title": rec.title,
        "proposal": rec.proposal,
        "rationale": rec.rationale,
        "evidence": rec.evidence,
        "status": rec.status,
        "source": rec.source,
        "created_at": rec.created_at,
        "decided_at": rec.decided_at,
        "decided_by": rec.decided_by,
        "applied_at": rec.applied_at,
        "apply_error": rec.apply_error,
        "previous_values": rec.previous_values,
        "current_values": current_values
    }

@router.get("/recommendations", response_model=List[RecommendationResponse])
def list_recommendations(db: DB, user: CurrentUser, status: Optional[str] = None, fanpage_id: Optional[int] = None, limit: int = 50):
    q = db.query(StrategyRecommendation, TargetFanpage).outerjoin(
        TargetFanpage, StrategyRecommendation.fanpage_id == TargetFanpage.id
    )
    if status:
        q = q.filter(StrategyRecommendation.status == status)
    if fanpage_id:
        q = q.filter(StrategyRecommendation.fanpage_id == fanpage_id)
        
    results = q.order_by(StrategyRecommendation.created_at.desc()).limit(limit).all()
    
    return [_build_recommendation_response(rec, fp) for rec, fp in results]

@router.post("/recommendations/{rec_id}/approve", response_model=RecommendationResponse)
def approve_recommendation(rec_id: int, db: DB, user: CurrentUser):
    rec = db.query(StrategyRecommendation).filter(StrategyRecommendation.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
        
    try:
        rec = strategy.approve(db, rec, user)
    except strategy.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
        
    fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == rec.fanpage_id).first()
    return _build_recommendation_response(rec, fanpage)

@router.post("/recommendations/{rec_id}/reject", response_model=RecommendationResponse)
def reject_recommendation(rec_id: int, db: DB, user: CurrentUser):
    rec = db.query(StrategyRecommendation).filter(StrategyRecommendation.id == rec_id).first()
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found")
        
    try:
        rec = strategy.reject(db, rec, user)
    except strategy.ConflictError as e:
        raise HTTPException(status_code=409, detail=str(e))
        
    fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == rec.fanpage_id).first()
    return _build_recommendation_response(rec, fanpage)
