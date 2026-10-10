import json
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from app.models.strategy import StrategyRecommendation
from app.models.target_fanpages import TargetFanpage

APPLICABLE_KINDS = ("sleep_window", "daily_cap")
INFO_KINDS = ("best_hours", "content_mix", "topic", "other")

# Fanpage fields each applicable kind may change (checked again at apply time).
_APPLY_KEYS = {
    "sleep_window": ("publish_sleep_start_hour", "publish_sleep_end_hour"),
    "daily_cap": ("publish_daily_limit",),
}


def validate_proposal(kind: str, proposal: dict) -> dict:
    if kind == "sleep_window":
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be a dictionary")
        if set(proposal.keys()) != {"publish_sleep_start_hour", "publish_sleep_end_hour"}:
            raise ValueError("sleep_window proposal must have exactly 'publish_sleep_start_hour' and 'publish_sleep_end_hour'")
        
        start = proposal.get("publish_sleep_start_hour")
        end = proposal.get("publish_sleep_end_hour")
        
        if start is None and end is None:
            pass
        elif start is not None and end is not None:
            if isinstance(start, bool) or isinstance(end, bool) or not isinstance(start, int) or not isinstance(end, int):
                raise ValueError("hours must be integers or null")
            if not (0 <= start <= 23) or not (0 <= end <= 23):
                raise ValueError("hours must be between 0 and 23")
            if start == end:
                raise ValueError("start and end hours must not be the same")
        else:
            raise ValueError("both hours must be null or both must be set")
            
    elif kind == "daily_cap":
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be a dictionary")
        if set(proposal.keys()) != {"publish_daily_limit"}:
            raise ValueError("daily_cap proposal must have exactly 'publish_daily_limit'")
            
        limit = proposal.get("publish_daily_limit")
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if not (1 <= limit <= 100):
            raise ValueError("limit must be between 1 and 100")
            
    elif kind in INFO_KINDS:
        if not isinstance(proposal, dict):
            raise ValueError("proposal must be a dictionary")
        if len(json.dumps(proposal)) > 4096:
            raise ValueError("proposal size exceeds 4 KB limit")
            
    else:
        raise ValueError(f"unknown kind: {kind}")
        
    return proposal


class LimitExceededError(Exception):
    pass


def create_recommendation(db: Session, fanpage_id: int, kind: str, title: str, proposal: dict, rationale: str, evidence: dict, token_id: int) -> StrategyRecommendation:
    # validate
    validate_proposal(kind, proposal)
    
    # check limits
    proposed_count = db.query(StrategyRecommendation).filter(
        StrategyRecommendation.fanpage_id == fanpage_id,
        StrategyRecommendation.status == "proposed"
    ).count()
    if proposed_count >= 20:
        raise LimitExceededError("Maximum of 20 proposed recommendations allowed per fanpage")
        
    # supersede older if applicable
    if kind in APPLICABLE_KINDS:
        older = db.query(StrategyRecommendation).filter(
            StrategyRecommendation.fanpage_id == fanpage_id,
            StrategyRecommendation.kind == kind,
            StrategyRecommendation.status == "proposed"
        ).all()
        for o in older:
            o.status = "superseded"
            
    rec = StrategyRecommendation(
        fanpage_id=fanpage_id,
        kind=kind,
        title=title,
        proposal=proposal,
        rationale=rationale,
        evidence=evidence,
        token_id=token_id,
        status="proposed",
        source="hermes"
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    return rec


class ConflictError(Exception):
    pass


def approve(db: Session, rec: StrategyRecommendation, username: str):
    if rec.status != "proposed":
        raise ConflictError(f"Cannot approve recommendation with status {rec.status}")
        
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rec.decided_at = now
    rec.decided_by = username
    
    if rec.kind in APPLICABLE_KINDS:
        try:
            fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == rec.fanpage_id).first()
            if not fanpage:
                raise Exception("Fanpage not found")
                
            prev = {}
            allowed = _APPLY_KEYS[rec.kind]
            for k, v in rec.proposal.items():
                if k not in allowed:
                    raise ValueError(f"Field {k} cannot be changed by a {rec.kind} recommendation")
                prev[k] = getattr(fanpage, k)
                setattr(fanpage, k, v)
                
            rec.previous_values = prev
            rec.status = "applied"
            rec.applied_at = now
        except Exception as e:
            db.rollback()
            rec.status = "failed"
            rec.apply_error = str(e)
    else:
        rec.status = "approved"
        
    db.commit()
    db.refresh(rec)
    return rec


def reject(db: Session, rec: StrategyRecommendation, username: str):
    if rec.status != "proposed":
        raise ConflictError(f"Cannot reject recommendation with status {rec.status}")
        
    rec.status = "rejected"
    rec.decided_at = datetime.now(timezone.utc).replace(tzinfo=None)
    rec.decided_by = username
    db.commit()
    db.refresh(rec)
    return rec
