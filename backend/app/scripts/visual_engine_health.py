"""Print visual engine (ChatGPT image) health from database."""

import argparse
import json
import logging
import re
from datetime import datetime, timezone, timedelta

from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.ai_copy_events import AICopyEvent
from app.services.ig_viewer_sanitize import sanitize_error
from app.services.codex_budget import get_codex_usage

logger = logging.getLogger(__name__)


def _is_recent(dt: datetime | None, cutoff_naive: datetime) -> bool:
    if dt is None:
        return False
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None) >= cutoff_naive
    return dt >= cutoff_naive


def _extract_chat_url(err_msg: str) -> str | None:
    m = re.search(r"chat_url=([^\s;]+)", err_msg)
    if not m:
        return None
    url = m.group(1).strip().strip("'\"")
    return sanitize_error(url) if url else None


def _extract_refusal_snippet(err_msg: str) -> str:
    prefix = "gptcli:refused:"
    idx = err_msg.find(prefix)
    if idx == -1:
        return ""
    raw = err_msg[idx + len(prefix):]
    if raw.startswith(" "):
        raw = raw[1:]
    clean = re.sub(r";?\s*chat_url=\S+.*$", "", raw).strip()
    text = clean if clean else raw
    return sanitize_error(text, limit=300)[:300]


def get_health(db: Session, now: datetime | None = None) -> dict:
    if now is None:
        now = datetime.now(timezone.utc)
        
    now_naive = now.replace(tzinfo=None)
    cutoff = now_naive - timedelta(hours=6)
    
    events = db.query(AICopyEvent).filter(
        AICopyEvent.context == "image_gen"
    ).order_by(AICopyEvent.id.desc()).limit(20).all()
    
    recent_events = [ev for ev in events if _is_recent(getattr(ev, "created_at", None), cutoff)]
    
    total = len(recent_events)
    failed = sum(1 for ev in recent_events if getattr(ev, "outcome", None) == "failed")
    fallback = sum(1 for ev in recent_events if getattr(ev, "outcome", None) == "recovered")
    
    consecutive_failures = 0
    last_error = ""
    for ev in recent_events:
        outcome = getattr(ev, "outcome", None)
        if outcome == "deferred":
            continue
        if outcome == "failed":
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

    # 6-hour window query for gpt-cli engine metrics
    window_events = db.query(AICopyEvent).filter(
        AICopyEvent.context.in_(["image_gen", "image_identity"]),
        AICopyEvent.created_at >= cutoff
    ).order_by(AICopyEvent.id.desc()).all()

    recent_window_events = [
        ev for ev in window_events
        if not isinstance(getattr(ev, "created_at", None), datetime)
        or _is_recent(ev.created_at, cutoff)
    ]
    gen_events = [ev for ev in recent_window_events if getattr(ev, "context", None) == "image_gen"]
    identity_events = [ev for ev in recent_window_events if getattr(ev, "context", None) == "image_identity"]

    by_engine = {
        "gptcli": {"success": 0, "recovered": 0, "failed": 0},
        "9router": {"success": 0, "recovered": 0, "failed": 0},
        "none": {"success": 0, "recovered": 0, "failed": 0},
        "deferred": 0,
    }
    gptcli_errors: dict[str, int] = {}
    refusals: list[dict] = []
    cloudflare: list[dict] = []

    for ev in gen_events:
        outcome = getattr(ev, "outcome", None)
        if outcome == "deferred":
            by_engine["deferred"] += 1
        elif outcome in ("success", "recovered", "failed"):
            prov = getattr(ev, "final_provider", None)
            if prov not in ("gptcli", "9router"):
                prov = "none"
            by_engine[prov][outcome] += 1

        err = getattr(ev, "error_message", None) or ""
        for err_type in re.findall(r"gptcli:([a-z_]+):", err):
            gptcli_errors[err_type] = gptcli_errors.get(err_type, 0) + 1

        dt = getattr(ev, "created_at", None)
        at_str = dt.isoformat() if hasattr(dt, "isoformat") else (str(dt) if dt else None)

        if "gptcli:refused:" in err and len(refusals) < 10:
            refusals.append({
                "id": ev.id,
                "at": at_str,
                "fanpage_id": getattr(ev, "fanpage_id", None),
                "snippet": _extract_refusal_snippet(err),
                "chat_url": _extract_chat_url(err),
            })

        if "gptcli:cloudflare_challenge" in err and len(cloudflare) < 10:
            cloudflare.append({
                "id": ev.id,
                "at": at_str,
                "fanpage_id": getattr(ev, "fanpage_id", None),
                "chat_url": _extract_chat_url(err),
            })

    identity_rejects = sum(
        1 for ev in identity_events if getattr(ev, "outcome", None) == "failed"
    )

    codex_usage = None
    try:
        usage = get_codex_usage(db, now)
        if usage is not None:
            codex_usage = {
                "window_pct": usage.window_pct,
                "weekly_pct": usage.weekly_pct,
                "resets_at": usage.resets_at.isoformat() if getattr(usage, "resets_at", None) else None,
                "source": usage.source,
            }
    except Exception:
        codex_usage = None

    return {
        "generated_at": now.isoformat(),
        "window": "6h",
        "total": total,
        "failed": failed,
        "fallback": fallback,
        "consecutive_failures": consecutive_failures,
        "last_error": last_error,
        "unhealthy": unhealthy,
        "by_engine": by_engine,
        "gptcli_errors": gptcli_errors,
        "refusals": refusals,
        "cloudflare": cloudflare,
        "identity_rejects": identity_rejects,
        "codex_usage": codex_usage,
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
