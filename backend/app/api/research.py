"""Research desk API for Muse (docs/research-desk-api.md). Nothing here may expose page names or publishing data."""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from app.api.deps import DB, require_hermes_scope
from app.models.ai_video import VideoIdea
from app.services import research_desk

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/research", tags=["research"])

# Muse's existing studio token (gallery labeler) works during the pilot; "research" is the long-term scope.
muse_token = require_hermes_scope("research", "labels:write")
reviewer_token = require_hermes_scope("research:review")


class PacksRequest(BaseModel):
    brief_id: str = Field("", max_length=64)
    packs: list[Any] = Field(min_length=1, max_length=research_desk.MAX_PACKS_PER_REQUEST)
    run_report: Optional[dict] = None


class NoteRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    client_ref: Optional[str] = Field(None, max_length=64)


class ReviewFeedbackRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    client_ref: Optional[str] = Field(None, max_length=64)
    idea_id: Optional[int] = None
    reject: bool = False


def _naive_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is not None and value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


@router.get("/brief")
def brief(db: DB, token=Depends(muse_token)):
    body = research_desk.build_brief(db)
    detail = {"reason": body.get("reason")} if body["action"] == "idle" else {
        "packs_needed": {n["niche"]: n["packs_needed"] for n in body["niches"]}}
    research_desk.log_call(db, "brief", body["action"], detail, token.id)
    return body


@router.get("/docs", response_class=PlainTextResponse)
def docs(token=Depends(muse_token)):
    return PlainTextResponse(research_desk.docs_text(), media_type="text/markdown; charset=utf-8")


@router.get("/schema")
def schema(token=Depends(muse_token)):
    return research_desk.pack_schema()


@router.post("/packs")
def submit_packs(body: PacksRequest, db: DB, token=Depends(muse_token)):
    row = research_desk.get_settings_row(db)
    results = [research_desk.submit_pack(db, pack, body.brief_id, row, token.id) for pack in body.packs]
    research_desk.save_run_report(db, row, body.run_report)
    variants = [v for r in results for v in r["variants"]]
    detail = {
        "brief_id": body.brief_id,
        "packs": [{"client_ref": r["client_ref"], "status": r["status"], "errors": r["errors"][:3]} for r in results],
        "variants_created": sum(1 for v in variants if v["status"] == "created"),
        "variants_rejected": sum(1 for v in variants if v["status"] == "rejected"),
        "rejections": [{"channel_id": v.get("channel_id"), "errors": v["errors"][:3]} for v in variants if v["status"] == "rejected"][:20],
    }
    research_desk.log_call(db, "packs", "submitted", detail, token.id)
    logger.info("Research desk packs: %s", {k: detail[k] for k in ("variants_created", "variants_rejected")})
    return {"results": results}


@router.get("/packs")
def list_packs(db: DB, token=Depends(muse_token), client_ref: Optional[str] = Query(None, max_length=64),
               since: Optional[datetime] = None):
    return research_desk.list_packs(db, client_ref=client_ref, since=_naive_utc(since))


@router.get("/feedback")
def feedback(db: DB, token=Depends(muse_token), since: Optional[datetime] = None):
    items = research_desk.feedback_for_muse(db, since=_naive_utc(since))
    research_desk.log_call(db, "feedback", "read", {"items": len(items)}, token.id)
    return items


@router.post("/notes", status_code=201)
def note(body: NoteRequest, db: DB, token=Depends(muse_token)):
    item = research_desk.add_feedback(db, author="muse", kind="note", message=body.message,
                                      client_ref=body.client_ref, token_id=token.id)
    research_desk.log_call(db, "notes", "note", {"message": body.message[:500], "client_ref": body.client_ref}, token.id)
    return {"id": item.id}


@router.get("/review/packs")
def review_packs(db: DB, token=Depends(reviewer_token), status: Optional[str] = Query(None, max_length=16),
                 since: Optional[datetime] = None, limit: int = Query(30, ge=1, le=100)):
    return research_desk.review_packs(db, status=status, since=_naive_utc(since), limit=limit)


@router.post("/review/feedback", status_code=201)
def review_feedback(body: ReviewFeedbackRequest, db: DB, token=Depends(reviewer_token)):
    if body.idea_id is None and not body.client_ref:
        raise HTTPException(422, "Give idea_id or client_ref")
    ideas = []
    if body.idea_id is not None:
        idea = db.query(VideoIdea).filter_by(id=body.idea_id, source="muse").first()
        if not idea:
            raise HTTPException(404, "Research idea not found")
        ideas = [idea]
        client_ref = idea.client_ref
    else:
        ideas = db.query(VideoIdea).filter_by(client_ref=body.client_ref, source="muse").all()
        if not ideas:
            raise HTTPException(404, "Research pack not found")
        client_ref = body.client_ref
    username = f"reviewer:{token.name}"[:64]
    rejected = []
    if body.reject:
        for idea in ideas:
            if idea.status in ("pilot", "proposed", "approved"):
                research_desk.reject_for_review(db, idea, username, None)
                rejected.append(idea.id)
    item = research_desk.add_feedback(
        db, author="studio", kind="rejected" if rejected else "comment", message=body.message,
        client_ref=client_ref, idea_id=body.idea_id, username=username, token_id=token.id,
    )
    return {"feedback_id": item.id, "rejected": rejected}
