from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Query
from typing import Optional
from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/logs", tags=["logs"])


@router.get("")
def get_logs(
    db: DB,
    _: CurrentUser,
    category: Optional[str] = Query(None),  # burner | publish | ai | all
    days: int = Query(30, le=90),
):
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.target_fanpages import TargetFanpage
    from app.models.ai_copy_events import AICopyEvent

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    items = []

    # ── Burner account issues ────────────────────────────────────────────────
    if category in (None, "burner"):
        burners = db.query(BurnerAccount).all()
        for b in burners:
            updated = b.updated_at or now
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)

            if b.status == BurnerStatus.challenged:
                items.append({
                    "id": f"burner_challenged_{b.id}",
                    "category": "burner",
                    "type": "challenge",
                    "severity": "warning",
                    "title": f"@{b.ig_username} — OTP required",
                    "message": b.last_error or "Instagram is challenging this account. Submit OTP to restore access.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })
            elif b.status == BurnerStatus.banned:
                items.append({
                    "id": f"burner_banned_{b.id}",
                    "category": "burner",
                    "type": "ban",
                    "severity": "error",
                    "title": f"@{b.ig_username} — Account banned",
                    "message": b.last_error or "This burner account has been banned by Instagram.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })
            elif b.status == BurnerStatus.rate_limited:
                items.append({
                    "id": f"burner_rate_{b.id}",
                    "category": "burner",
                    "type": "rate_limit",
                    "severity": "warning",
                    "title": f"@{b.ig_username} — Rate limited",
                    "message": b.last_error or "Account hit Instagram request limits. Will auto-reset tomorrow.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })
            elif b.last_error and b.status == BurnerStatus.active:
                items.append({
                    "id": f"burner_error_{b.id}",
                    "category": "burner",
                    "type": "session_error",
                    "severity": "error",
                    "title": f"@{b.ig_username} — Session error",
                    "message": b.last_error,
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })

            # No session at all
            if not b.encrypted_session and b.status == BurnerStatus.active:
                items.append({
                    "id": f"burner_nosession_{b.id}",
                    "category": "burner",
                    "type": "no_session",
                    "severity": "warning",
                    "title": f"@{b.ig_username} — No session",
                    "message": "No Instagram session imported. Crawling won't work until a session is set.",
                    "account": b.ig_username,
                    "occurred_at": updated.isoformat(),
                    "link": "/burners",
                })

    # ── Failed publish jobs ──────────────────────────────────────────────────
    if category in (None, "publish"):
        failed_jobs = (
            db.query(PublishJob)
            .filter(
                PublishJob.status == PublishJobStatus.failed,
                PublishJob.updated_at >= cutoff,
            )
            .order_by(PublishJob.updated_at.desc())
            .limit(100)
            .all()
        )
        for job in failed_jobs:
            fanpage = db.query(TargetFanpage).filter_by(id=job.fanpage_id).first()
            fanpage_name = fanpage.name if fanpage else f"Fanpage #{job.fanpage_id}"
            updated = job.updated_at or now
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)

            items.append({
                "id": f"job_failed_{job.id}",
                "category": "publish",
                "type": "publish_failed",
                "severity": "error",
                "title": f"Post failed — {fanpage_name}",
                "message": job.last_error or "Publish job failed without an error message.",
                "account": fanpage_name,
                "occurred_at": updated.isoformat(),
                "link": "/history",
            })

    # ── AI copy generation degradation/failures ──────────────────────────────
    # Only "recovered" (fallback saved the post, but 9Router is degraded — a
    # heads-up) and "failed" (post permanently lost) events surface as items;
    # "success" rows only feed ai_stats below. See the 2026-08-16 incident
    # this table was built for: My-Combo silently truncating output dropped
    # Mode 2's success rate to 1.1% with no dashboard signal.
    if category in (None, "ai"):
        ai_events = (
            db.query(AICopyEvent)
            .filter(
                AICopyEvent.outcome.in_(["recovered", "failed"]),
                AICopyEvent.created_at >= cutoff,
            )
            .order_by(AICopyEvent.created_at.desc())
            .limit(100)
            .all()
        )
        # Text contexts create a PublishJob directly — a "failed" event there
        # means the post is genuinely lost. Vision contexts (image crop/pick/
        # match) fail OPEN by design (falls back to OpenCV or a default) — a
        # "failed" event there just means a lower-quality fallback was used,
        # never a lost post, so it's surfaced as a lighter warning, not error.
        _AI_CONTEXT_META = {
            "news_copy":               {"label": "News post",           "is_text": True},
            "discussion_copy":         {"label": "Discussion card",     "is_text": True},
            "vision_focus_point":      {"label": "Photo crop focus",    "is_text": False},
            "vision_classify_type":    {"label": "Photo classification", "is_text": False},
            "vision_classify_closeup": {"label": "Gallery AI filter",   "is_text": False},
            "vision_pick_best":        {"label": "Best-photo picker",   "is_text": False},
            "vision_verify_match":     {"label": "Photo match check",   "is_text": False},
        }

        for ev in ai_events:
            meta = _AI_CONTEXT_META.get(ev.context, {"label": ev.context, "is_text": True})
            context_label = meta["label"]
            is_text = meta["is_text"]

            fanpage = db.query(TargetFanpage).filter_by(id=ev.fanpage_id).first() if ev.fanpage_id else None
            fanpage_name = fanpage.name if fanpage else (f"Fanpage #{ev.fanpage_id}" if ev.fanpage_id else context_label)
            created = ev.created_at or now
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            if ev.outcome == "failed" and is_text:
                items.append({
                    "id": f"ai_failed_{ev.id}",
                    "category": "ai",
                    "type": "ai_failed",
                    "severity": "error",
                    "title": f"{context_label} lost — {fanpage_name}",
                    "message": ev.error_message or "Every AI model/provider failed — no post was created for this article.",
                    "account": fanpage_name,
                    "occurred_at": created.isoformat(),
                    "link": "/settings",
                })
            elif ev.outcome == "failed":
                items.append({
                    "id": f"ai_failed_{ev.id}",
                    "category": "ai",
                    "type": "ai_recovered",
                    "severity": "warning",
                    "title": f"{context_label} fell back to default — {fanpage_name}",
                    "message": f"All vision models failed (tried: {ev.models_tried}) — used an automatic fallback, no post was lost. "
                               f"{ev.error_message or ''}".strip(),
                    "account": fanpage_name,
                    "occurred_at": created.isoformat(),
                    "link": "/settings",
                })
            else:
                items.append({
                    "id": f"ai_recovered_{ev.id}",
                    "category": "ai",
                    "type": "ai_recovered",
                    "severity": "warning",
                    "title": f"9Router degraded — {fanpage_name}",
                    "message": f"Primary model failed (tried: {ev.models_tried}), recovered via {ev.final_provider}. "
                               f"{ev.error_message or ''}".strip(),
                    "account": fanpage_name,
                    "occurred_at": created.isoformat(),
                    "link": "/settings",
                })

    # Sort newest first
    items.sort(key=lambda x: x["occurred_at"], reverse=True)

    error_count   = sum(1 for i in items if i["severity"] == "error")
    warning_count = sum(1 for i in items if i["severity"] == "warning")

    # ── AI success-rate summary (always computed, for the dashboard stat row) ──
    ai_window = db.query(AICopyEvent).filter(AICopyEvent.created_at >= cutoff).all()
    ai_total = len(ai_window)
    ai_success = sum(1 for e in ai_window if e.outcome == "success")
    ai_recovered = sum(1 for e in ai_window if e.outcome == "recovered")
    ai_failed = sum(1 for e in ai_window if e.outcome == "failed")
    ai_stats = {
        "total": ai_total,
        "success": ai_success,
        "recovered": ai_recovered,
        "failed": ai_failed,
        "success_rate": round(100 * (ai_success + ai_recovered) / ai_total, 1) if ai_total else None,
    }

    return {
        "logs": items,
        "total": len(items),
        "error_count": error_count,
        "warning_count": warning_count,
        "ai_stats": ai_stats,
    }
