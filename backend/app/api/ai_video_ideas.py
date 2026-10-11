from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DB
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_ideas
from app.services.video_templates import list_templates, liveries, narrator_presets, required_variables, vehicle_presets

router = APIRouter(prefix="/ai-video", tags=["ai-video"])


class IdeaCreate(BaseModel):
    fanpage_id: int
    template_key: str
    topic: str
    angle: Optional[str] = None
    research_notes: Optional[str] = None
    clips: Optional[int] = None
    variables: Optional[dict] = None


def _item(db, idea):
    fanpage = db.query(TargetFanpage).filter_by(id=idea.fanpage_id).first()
    job = db.query(VideoJob).filter_by(idea_id=idea.id).first()
    return {"id": idea.id, "fanpage_id": idea.fanpage_id, "fanpage_name": fanpage.name if fanpage else "", "template_key": idea.template_key, "topic": idea.topic, "angle": idea.angle, "research_notes": idea.research_notes, "variables": idea.variables, "clips": idea.clips, "score": idea.score, "rationale": idea.rationale, "evidence": idea.evidence, "source": idea.source, "status": idea.status, "error": idea.error, "created_at": idea.created_at, "decided_at": idea.decided_at, "decided_by": idea.decided_by, "video_job_id": job.id if job else None}


@router.get("/templates")
def templates(_: CurrentUser):
    return [{"key": t.key, "name": t.name, "niches": list(t.niches), "clip_options": list(t.clip_options), "default_clips": t.default_clips, "clip_seconds": t.clip_seconds, "credits_per_clip": t.credits_per_clip, "model": t.model, "variables_by_clips": {str(n): required_variables(t, n) for n in t.clip_options}, "vehicle_presets": vehicle_presets(t), "liveries": liveries(t), "narrator_presets": narrator_presets(t), "topic_ideas": t.raw.get("topic_ideas", [])} for t in list_templates()]


@router.get("/ideas")
def list_ideas(db: DB, _: CurrentUser, status: Optional[str] = None, fanpage_id: Optional[int] = None, limit: int = Query(50, ge=1, le=200)):
    q = db.query(VideoIdea)
    if status: q = q.filter(VideoIdea.status == status)
    if fanpage_id: q = q.filter(VideoIdea.fanpage_id == fanpage_id)
    return [_item(db, idea) for idea in q.order_by(VideoIdea.created_at.desc()).limit(limit).all()]


@router.post("/ideas", status_code=201)
def create_idea(body: IdeaCreate, db: DB, user: CurrentUser):
    try:
        idea = ai_video_ideas.create_idea(db, body.fanpage_id, body.model_dump(), "owner", username=user)
    except ai_video_ideas.ConflictError as exc: raise HTTPException(409, str(exc))
    except ai_video_ideas.LimitExceededError as exc: raise HTTPException(429, str(exc))
    except (ValueError, KeyError) as exc: raise HTTPException(422, str(exc))
    return _item(db, idea)


def _decide(idea_id, db, user, action):
    idea = db.query(VideoIdea).filter_by(id=idea_id).first()
    if not idea: raise HTTPException(404, "Idea not found")
    try: idea = getattr(ai_video_ideas, action)(db, idea, user)
    except ai_video_ideas.ConflictError as exc: raise HTTPException(409, str(exc))
    return _item(db, idea)


@router.post("/ideas/{idea_id}/approve")
def approve_idea(idea_id: int, db: DB, user: CurrentUser): return _decide(idea_id, db, user, "approve")


@router.post("/ideas/{idea_id}/reject")
def reject_idea(idea_id: int, db: DB, user: CurrentUser): return _decide(idea_id, db, user, "reject")
