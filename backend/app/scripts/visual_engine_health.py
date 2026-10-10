"""Print visual engine (ChatGPT image) health from database."""

import argparse
import json
import logging
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.ai_copy_events import AICopyEvent
from app.services.ig_viewer_sanitize import sanitize_error

logger = logging.getLogger(__name__)


def get_health(db: Session, now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)
        
    now_naive = now.replace(tzinfo=None)
    cutoff = now_naive - timedelta(hours=6)
    
    events = db.query(AICopyEvent).filter(
        AICopyEvent.context == "image_gen"
    ).order_by(AICopyEvent.id.desc()).limit(20).all()
    
    recent_events = [ev for ev in events if ev.created_at >= cutoff]
    
    total = len(recent_events)
    failed = sum(1 for ev in recent_events if ev.outcome == "failed")
    fallback = sum(1 for ev in recent_events if ev.outcome == "recovered")
    
    consecutive_failures = 0
    last_error = ""
    for ev in recent_events:
        if ev.outcome == "failed":
            if consecutive_failures == 0:
                last_error = sanitize_error(ev.error_message or "")
            consecutive_failures += 1
        else:
            break
            
    unhealthy = False
    if total > 0:
        if consecutive_failures >= 5:
            unhealthy = True
        elif total >= 20 and (fallback / total) >= 0.5:
            unhealthy = True

    return {
        "generated_at": now.isoformat(),
        "window": "6h",
        "total": total,
        "failed": failed,
        "fallback": fallback,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "unhealthy": unhealthy
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    
    db = None
    try:
        db = SessionLocal()
        health = get_health(db)
    except Exception as exc:
        logger.warning("Visual engine health unavailable: %s", type(exc).__name__)
        print(json.dumps({"error": "health_unavailable"}))
        return 2
    finally:
        if db is not None:
            db.close()
            
    print(json.dumps(health, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
