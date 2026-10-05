"""Dashboard snapshot for login-free Instagram viewer tiers."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter

from app.api.deps import CurrentUser, DB
from app.models.settings import Settings
from app.services.ig_viewer_health import TIERS, get_health
from app.services.ig_viewer_sanitize import sanitize_error

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scraper-health", tags=["scraper-health"])


@router.get("")
def scraper_health(db: DB, _: CurrentUser):
    generated_at = datetime.now(timezone.utc).isoformat()
    scraper_mode = "auto"
    try:
        row = db.query(Settings).filter_by(id=1).first()
        scraper_mode = (row.scraper_mode if row and row.scraper_mode else "auto")
        if scraper_mode == "flashapi":
            scraper_mode = "viewer"
        snapshot = get_health(strict=False)
        tiers = {}
        for tier in TIERS:
            state = snapshot["tiers"][tier]
            failures = state["consecutive_failures"]
            recorded = any(state.get(field) for field in (
                "last_success_at", "last_failure_at", "streak_started_at",
                "last_error_kind", "last_error")) or failures
            events = []
            for ev in state.get("events", []):
                events.append({
                    "at": ev.get("at"),
                    "username": ev.get("username"),
                    "ok": ev.get("ok"),
                    "kind": ev.get("kind"),
                    "reason": sanitize_error(ev.get("reason") or ""),
                    "detail": sanitize_error(ev.get("detail") or "") if ev.get("detail") else None
                })
            tiers[tier] = {
                "status": ("unhealthy" if state["unhealthy"] else "degraded" if failures > 0
                           else "healthy" if recorded else "unknown"),
                "last_success_at": state["last_success_at"],
                "last_failure_at": state["last_failure_at"],
                "last_failure_username": state.get("last_failure_username", ""),
                "consecutive_failures": failures,
                "distinct_users": state["distinct_users"],
                "last_error_kind": state["last_error_kind"],
                "last_error": sanitize_error(state["last_error"]),
                "unhealthy": state["unhealthy"],
                "events": events
            }
        return {"available": True, "scraper_mode": scraper_mode,
                "generated_at": snapshot["generated_at"], "tiers": tiers}
    except Exception as exc:
        logger.warning("Scraper health unavailable: %s", type(exc).__name__)
        return {"available": False, "scraper_mode": scraper_mode,
                "generated_at": generated_at, "tiers": {}}
