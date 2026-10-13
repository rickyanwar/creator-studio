"""Validation and lifecycle operations for Mode 8 video ideas."""
import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.models.ai_video import VideoIdea
from app.models.target_fanpages import TargetFanpage
from app.services.video_templates import allowed_video_templates, get_template


class ConflictError(Exception):
    pass


class LimitExceededError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _validate(data: dict) -> None:
    template = get_template(data.get("template_key", ""))
    topic = data.get("topic")
    if not isinstance(topic, str) or not 3 <= len(topic.strip()) <= 200:
        raise ValueError("topic must be 3-200 characters")
    for key, limit in (("angle", 2000), ("research_notes", 8000), ("rationale", 4000)):
        value = data.get(key)
        if value is not None and (not isinstance(value, str) or len(value) > limit):
            raise ValueError(f"{key} exceeds {limit} characters")
    clips = data.get("clips")
    if clips is not None and clips not in template.clip_options:
        raise ValueError(f"clips must be one of {template.clip_options}")
    score = data.get("score")
    if score is not None and (isinstance(score, bool) or not isinstance(score, int) or not 0 <= score <= 100):
        raise ValueError("score must be between 0 and 100")
    for key, limit in (("evidence", 4096), ("variables", 8192)):
        value = data.get(key)
        if value is not None:
            if not isinstance(value, dict):
                raise ValueError(f"{key} must be an object")
            if len(json.dumps(value, ensure_ascii=False).encode("utf-8")) > limit:
                raise ValueError(f"{key} exceeds {limit} bytes")


def _normalized_topic(topic: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", "", topic.lower())).strip()


def create_idea(db: Session, fanpage_id: int, data: dict, source: str, token_id=None, username=None) -> VideoIdea:
    _validate(data)
    fanpage = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if fanpage:
        allowed = allowed_video_templates(fanpage)
        if allowed and data.get("template_key") not in allowed:
            raise ValueError(f"template_key '{data.get('template_key')}' is not allowed for fanpage {fanpage_id}")
    if source in ("hermes", "ai") and (data.get("score") is None or not data.get("rationale")):
        raise ValueError("Hermes and AI ideas require score and rationale")
    proposed = db.query(VideoIdea).filter_by(fanpage_id=fanpage_id, status="proposed").count()
    if proposed >= 30:
        raise LimitExceededError("Maximum of 30 proposed ideas allowed per fanpage")
    cutoff = _now() - timedelta(days=30)
    normalized = _normalized_topic(data["topic"])
    for idea in db.query(VideoIdea).filter(VideoIdea.created_at >= cutoff, VideoIdea.status != "rejected").all():
        if _normalized_topic(idea.topic) == normalized:
            raise ConflictError(f"topic already used on fanpage {idea.fanpage_id} in the last 30 days")
    approved = source == "owner"
    idea = VideoIdea(
        fanpage_id=fanpage_id, template_key=data["template_key"], topic=data["topic"].strip(),
        angle=data.get("angle"), research_notes=data.get("research_notes"), variables=data.get("variables"),
        clips=data.get("clips"), score=data.get("score"), rationale=data.get("rationale"),
        evidence=data.get("evidence"), source=source, token_id=token_id,
        status="approved" if approved else "proposed", decided_by=username if approved else None,
        decided_at=_now() if approved else None,
    )
    db.add(idea)
    db.commit()
    db.refresh(idea)
    return idea


def approve(db: Session, idea: VideoIdea, username: str) -> VideoIdea:
    if idea.status != "proposed":
        raise ConflictError(f"Cannot approve idea with status {idea.status}")
    idea.status, idea.decided_by, idea.decided_at = "approved", username, _now()
    db.commit()
    db.refresh(idea)
    return idea


def reject(db: Session, idea: VideoIdea, username: str) -> VideoIdea:
    if idea.status != "proposed":
        raise ConflictError(f"Cannot reject idea with status {idea.status}")
    idea.status, idea.decided_by, idea.decided_at = "rejected", username, _now()
    db.commit()
    db.refresh(idea)
    return idea
