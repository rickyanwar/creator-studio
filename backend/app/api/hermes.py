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

from typing import Dict, List, Optional
from fastapi import Query
from app.api.analytics import get_fanpage_analytics as internal_get_fanpage_analytics
from app.services import strategy
from app.models.strategy import StrategyRecommendation
from app.models.api_tokens import ApiToken
from app.models.ai_video import VideoIdea, VideoJob
from app.services import ai_video_ideas
from app.services.video_templates import (
    allowed_video_templates,
    clip_choices,
    get_template,
    list_templates,
    liveries,
    narrator_presets,
    required_variables,
    vehicle_presets,
)

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
    ai_video_enabled: bool
    ai_video_daily_count: int
    ai_video_publish_mode: str
    ai_video_template: Optional[str]
    ai_video_templates: List[str]
    ai_video_narrator: Optional[str]
    ai_video_clips: int
    ai_video_clips_min: Optional[int]
    ai_video_seconds_min: Optional[int] = None
    ai_video_seconds_max: Optional[int] = None
    ai_video_max_credits_per_video: Optional[int] = None
    ai_video_max_credits_per_day: Optional[int] = None
    ai_video_clip_choices: Dict[str, List[int]]

@router.get("/fanpages", response_model=List[FanpageHermesResponse])
def get_fanpages_hermes(db: DB, token: ApiToken = Depends(require_hermes_scope("read"))):
    fanpages = db.query(TargetFanpage).all()
    res = []
    for f in fanpages:
        clip_choices_map = {}
        for key in allowed_video_templates(f):
            try:
                clip_choices_map[key] = clip_choices(f, get_template(key))
            except ValueError:
                continue
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
            "caption_language": f.caption_language,
            "ai_video_enabled": f.ai_video_enabled,
            "ai_video_daily_count": f.ai_video_daily_count,
            "ai_video_publish_mode": f.ai_video_publish_mode.value,
            "ai_video_template": f.ai_video_template,
            "ai_video_templates": allowed_video_templates(f),
            "ai_video_narrator": f.ai_video_narrator,
            "ai_video_clips": f.ai_video_clips,
            "ai_video_clips_min": f.ai_video_clips_min,
            "ai_video_seconds_min": f.ai_video_seconds_min,
            "ai_video_seconds_max": f.ai_video_seconds_max,
            "ai_video_max_credits_per_video": f.ai_video_max_credits_per_video,
            "ai_video_max_credits_per_day": f.ai_video_max_credits_per_day,
            "ai_video_clip_choices": clip_choices_map,
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


def _video_template_items(templates=None):
    if templates is None:
        templates = list_templates()
    items = []
    for t in templates:
        raw = t.raw
        generation = raw.get("generation", {})
        item = {
            "key": t.key,
            "name": t.name,
            "niches": list(t.niches),
            "clip_options": list(t.clip_options),
            "default_clips": t.default_clips,
            "clip_seconds": t.clip_seconds,
            "credits_per_clip": t.credits_per_clip,
            "model": t.model,
            "variables_by_clips": {str(n): required_variables(t, n) for n in t.clip_options},
            "vehicle_presets": vehicle_presets(t),
            "liveries": liveries(t),
            "narrator_presets": narrator_presets(t),
            "topic_ideas": raw.get("topic_ideas", []),
            "description": raw.get("description", raw.get("purpose", "")),
            "purpose": raw.get("purpose", raw.get("description", "")),
            "status": raw.get("status"),
            "inspiration": raw.get("inspiration"),
            "example_output": raw.get("example_output"),
            "keywords_that_worked": raw.get("keywords_that_worked", []),
            "rules": raw.get("rules", []),
            "blocks": raw.get("blocks", {}),
            "generation": generation,
        }
        items.append(item)
    return items


@router.get("/video-templates")
def get_video_templates_hermes(
    fanpage_id: Optional[int] = Query(None),
    db: DB = None,
    token: ApiToken = Depends(require_hermes_scope("read")),
):
    all_templates = list_templates()
    if fanpage_id is not None:
        fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
        if not fanpage:
            raise HTTPException(404, "Fanpage not found")
        allowed = allowed_video_templates(fanpage)
        templates = [t for t in all_templates if t.key in allowed]
    else:
        templates = all_templates
    return _video_template_items(templates)


class VideoIdeaHermesRequest(BaseModel):
    fanpage_id: int
    template_key: str
    topic: str
    angle: Optional[str] = None
    research_notes: Optional[str] = None
    variables: Optional[dict] = None
    clips: Optional[int] = None
    score: Optional[int] = None
    rationale: Optional[str] = None
    evidence: Optional[dict] = None


def _video_idea_item(db, idea):
    fanpage = db.query(TargetFanpage).filter_by(id=idea.fanpage_id).first()
    job = db.query(VideoJob).filter_by(idea_id=idea.id).first()
    return {"id": idea.id, "fanpage_id": idea.fanpage_id, "fanpage_name": fanpage.name if fanpage else "", "template_key": idea.template_key, "topic": idea.topic, "angle": idea.angle, "research_notes": idea.research_notes, "variables": idea.variables, "clips": idea.clips, "score": idea.score, "rationale": idea.rationale, "evidence": idea.evidence, "source": idea.source, "status": idea.status, "error": idea.error, "created_at": idea.created_at, "decided_at": idea.decided_at, "decided_by": idea.decided_by, "video_job_id": job.id if job else None, "publish_job_id": job.publish_job_id if job and job.status == "done" else None, "video_url": job.video_url if job and job.status == "done" else None}


@router.post("/video-ideas", status_code=201)
def create_video_idea_hermes(body: VideoIdeaHermesRequest, db: DB, token: ApiToken = Depends(require_hermes_scope("ideas:write"))):
    if not db.query(TargetFanpage).filter_by(id=body.fanpage_id).first():
        raise HTTPException(404, "Fanpage not found")
    try:
        idea = ai_video_ideas.create_idea(db, body.fanpage_id, body.model_dump(), "hermes", token_id=token.id)
    except ai_video_ideas.ConflictError as exc: raise HTTPException(409, str(exc))
    except ai_video_ideas.LimitExceededError as exc: raise HTTPException(429, str(exc))
    except (ValueError, KeyError) as exc: raise HTTPException(422, str(exc))
    return _video_idea_item(db, idea)


@router.get("/video-ideas")
def list_video_ideas_hermes(db: DB, token: ApiToken = Depends(require_hermes_scope("read")), fanpage_id: Optional[int] = None, status: Optional[str] = None):
    q = db.query(VideoIdea)
    if fanpage_id: q = q.filter(VideoIdea.fanpage_id == fanpage_id)
    if status: q = q.filter(VideoIdea.status == status)
    return [_video_idea_item(db, idea) for idea in q.order_by(VideoIdea.created_at.desc()).limit(200).all()]


_model_health_read_scope = require_hermes_scope("read")


@router.get("/model-health")
def get_model_health(token: ApiToken = Depends(_model_health_read_scope)):
    """Current model health snapshot.

    Reads from Redis + one /v1/models call — never triggers probes.

    Response shape:
    {
      "chains": {
        "text":   [{"model", "status", "checked_at", "message"}, ...],
        "vision": [{"model", "status", "checked_at", "message"}, ...]
      },
      "primaries": {
        "text":       {"model", "status", "checked_at", "message"} | null,
        "discussion": {"model", "status", "checked_at", "message"} | null,
        "vision":     {"model", "status", "checked_at", "message"} | null
      },
      "dead":               [model_id, ...],
      "live_router_models": [model_id, ...],   // cx/* excluded
      "unused_live_models": [model_id, ...]    // ag/* live but in no chain
    }
    """
    from app.services.model_health import get_health_snapshot
    return get_health_snapshot()
