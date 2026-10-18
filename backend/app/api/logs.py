from datetime import datetime, timedelta, timezone
from typing import Optional
from fastapi import APIRouter, Query
from pydantic import BaseModel
from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/logs", tags=["logs"])


class MarkHandledRequest(BaseModel):
    entry_kind: str
    entry_id: int


@router.get("")
def get_logs(
    db: DB,
    _: CurrentUser,
    category: Optional[str] = Query(None),  # publish | ai | access | all
    days: int = Query(30, le=90),
    fanpage_id: Optional[int] = Query(None),
):
    from app.models.publish_jobs import PublishJob, PublishJobStatus
    from app.models.target_fanpages import TargetFanpage
    from app.models.ai_copy_events import AICopyEvent
    from app.models.log_handled import LogHandled

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)
    items = []

    handled_rows = db.query(LogHandled.entry_kind, LogHandled.entry_id).all()
    handled_set = {(r.entry_kind, r.entry_id) for r in handled_rows}

    # ── Failed publish jobs ──────────────────────────────────────────────────
    if category in (None, "all", "publish"):
        job_query = (
            db.query(PublishJob)
            .filter(
                PublishJob.status == PublishJobStatus.failed,
                PublishJob.updated_at >= cutoff,
            )
        )
        if fanpage_id is not None:
            job_query = job_query.filter(PublishJob.fanpage_id == fanpage_id)

        failed_jobs = (
            job_query
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
                "kind": "job",
                "entry_kind": "job",
                "entry_id": job.id,
                "category": "publish",
                "type": "publish_failed",
                "severity": "error",
                "title": f"Post failed — {fanpage_name}",
                "message": job.last_error or "Publish job failed without an error message.",
                "account": fanpage_name,
                "occurred_at": updated.isoformat(),
                "link": "/history",
                "handled": ("job", job.id) in handled_set,
            })

    # ── AI copy generation degradation/failures ──────────────────────────────
    if category in (None, "all", "ai"):
        ai_query = (
            db.query(AICopyEvent)
            .filter(
                AICopyEvent.outcome.in_(["recovered", "failed"]),
                AICopyEvent.created_at >= cutoff,
            )
        )
        if fanpage_id is not None:
            ai_query = ai_query.filter(AICopyEvent.fanpage_id == fanpage_id)

        ai_events = (
            ai_query
            .order_by(AICopyEvent.created_at.desc())
            .limit(100)
            .all()
        )

        _AI_CONTEXT_META = {
            "news_copy":               {"label": "News post",           "is_text": True},
            "discussion_copy":         {"label": "Discussion card",     "is_text": True},
            "yt_highlight":            {"label": "YouTube clip highlights", "is_text": True},
            "vision_focus_point":      {"label": "Photo crop focus",    "is_text": False},
            "vision_classify_type":    {"label": "Photo classification", "is_text": False},
            "vision_classify_closeup": {"label": "Gallery AI filter",   "is_text": False},
            "vision_pick_best":        {"label": "Best-photo picker",   "is_text": False},
            "vision_verify_match":     {"label": "Photo match check",   "is_text": False},
            "health_check_vision":     {"label": "Vision health check",  "is_text": False},
            "health_check_text":       {"label": "Text health check",   "is_text": False},
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

            is_handled = ("ai", ev.id) in handled_set

            if ev.outcome == "failed" and is_text:
                items.append({
                    "id": f"ai_failed_{ev.id}",
                    "kind": "ai",
                    "entry_kind": "ai",
                    "entry_id": ev.id,
                    "category": "ai",
                    "type": "ai_failed",
                    "severity": "error",
                    "title": f"{context_label} lost — {fanpage_name}",
                    "message": ev.error_message or "Every AI model/provider failed — no post was created for this article.",
                    "account": fanpage_name,
                    "occurred_at": created.isoformat(),
                    "link": "/settings",
                    "handled": is_handled,
                })
            elif ev.outcome == "failed":
                items.append({
                    "id": f"ai_failed_{ev.id}",
                    "kind": "ai",
                    "entry_kind": "ai",
                    "entry_id": ev.id,
                    "category": "ai",
                    "type": "ai_recovered",
                    "severity": "warning",
                    "title": f"{context_label} fell back to default — {fanpage_name}",
                    "message": f"All vision models failed (tried: {ev.models_tried}) — used an automatic fallback, no post was lost. "
                               f"{ev.error_message or ''}".strip(),
                    "account": fanpage_name,
                    "occurred_at": created.isoformat(),
                    "link": "/settings",
                    "handled": is_handled,
                })
            else:
                items.append({
                    "id": f"ai_recovered_{ev.id}",
                    "kind": "ai",
                    "entry_kind": "ai",
                    "entry_id": ev.id,
                    "category": "ai",
                    "type": "ai_recovered",
                    "severity": "warning",
                    "title": f"9Router degraded — {fanpage_name}",
                    "message": f"Primary model failed (tried: {ev.models_tried}), recovered via {ev.final_provider}. "
                               f"{ev.error_message or ''}".strip(),
                    "account": fanpage_name,
                    "occurred_at": created.isoformat(),
                    "link": "/settings",
                    "handled": is_handled,
                })

    # ── Auth / access events ────────────────────────────────────────────────
    if category in (None, "all", "access") and (fanpage_id is None or category == "access"):
        from app.models.auth_events import AuthEvent

        auth_events = (
            db.query(AuthEvent)
            .filter(AuthEvent.created_at >= cutoff)
            .order_by(AuthEvent.created_at.desc())
            .limit(100)
            .all()
        )
        for aev in auth_events:
            created = aev.created_at or now
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)

            user_label = aev.username or "Unknown user"
            ip_label = aev.ip or "unknown IP"
            is_failed = not aev.success

            if is_failed:
                title = f"Failed login attempt — {user_label}"
                message = f"Failed login from {ip_label}"
                if aev.user_agent:
                    message += f" ({aev.user_agent})"
                severity = "warning"
                type_name = "login_failed"
            else:
                title = f"Login — {user_label}"
                message = f"Successful login from {ip_label}"
                if aev.user_agent:
                    message += f" ({aev.user_agent})"
                severity = "info"
                type_name = "login_success"

            items.append({
                "id": f"auth_{aev.id}",
                "kind": "auth",
                "entry_kind": "auth",
                "entry_id": aev.id,
                "category": "access",
                "type": type_name,
                "severity": severity,
                "title": title,
                "message": message,
                "account": user_label,
                "occurred_at": created.isoformat(),
                "link": None,
                "handled": ("auth", aev.id) in handled_set,
            })

    # Sort newest first
    items.sort(key=lambda x: x["occurred_at"], reverse=True)

    error_count   = sum(1 for i in items if i["severity"] == "error" and not i.get("handled"))
    warning_count = sum(1 for i in items if i["severity"] == "warning" and not i.get("handled"))

    # ── AI success-rate summary (always computed, for the dashboard stat row) ──
    ai_window_q = db.query(AICopyEvent).filter(AICopyEvent.created_at >= cutoff)
    if fanpage_id is not None:
        ai_window_q = ai_window_q.filter(AICopyEvent.fanpage_id == fanpage_id)
    ai_window = ai_window_q.all()
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


@router.post("/handled")
def mark_log_handled(body: MarkHandledRequest, db: DB, _: CurrentUser):
    from app.models.log_handled import LogHandled

    existing = (
        db.query(LogHandled)
        .filter_by(entry_kind=body.entry_kind, entry_id=body.entry_id)
        .first()
    )
    if not existing:
        record = LogHandled(entry_kind=body.entry_kind, entry_id=body.entry_id)
        db.add(record)
        db.commit()

    return {
        "ok": True,
        "entry_kind": body.entry_kind,
        "entry_id": body.entry_id,
        "handled": True,
    }


@router.delete("/handled/{entry_kind}/{entry_id}")
def unmark_log_handled(entry_kind: str, entry_id: int, db: DB, _: CurrentUser):
    from app.models.log_handled import LogHandled

    existing = (
        db.query(LogHandled)
        .filter_by(entry_kind=entry_kind, entry_id=entry_id)
        .first()
    )
    if existing:
        db.delete(existing)
        db.commit()

    return {
        "ok": True,
        "entry_kind": entry_kind,
        "entry_id": entry_id,
        "handled": False,
    }
