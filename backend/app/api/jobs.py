"""Manual job triggers — for testing and admin actions."""

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/jobs", tags=["jobs"])

RESTART_BEAT_FLAG = "ops:restart_beat_requested"


@router.post("/crawl-now")
def trigger_crawl(_: CurrentUser):
    """Manually trigger a full crawl cycle."""
    from app.tasks.crawler import crawl_all_sources
    task = crawl_all_sources.delay(manual=True)
    return {"ok": True, "task_id": task.id}


@router.post("/sync-fanpages")
def trigger_fanpage_sync(_: CurrentUser):
    """Manually trigger a Repliz fanpage sync."""
    from app.tasks.fanpage_sync import sync_fanpages_from_repliz
    task = sync_fanpages_from_repliz.delay()
    return {"ok": True, "task_id": task.id}


@router.post("/cleanup-now")
def trigger_cleanup(_: CurrentUser):
    """Manually trigger media cleanup."""
    from app.tasks.cleanup import cleanup_old_media
    task = cleanup_old_media.delay()
    return {"ok": True, "task_id": task.id}


@router.post("/restart-beat")
def restart_beat(_: CurrentUser):
    """Request a Celery beat restart. The API no longer has the Docker socket (security, 2026-10-18):
    it only sets a Redis flag; the host timer studio-restart-beat.timer restarts beat within ~30 s."""
    try:
        import redis
        from datetime import datetime, timezone
        from app.config import get_settings
        r = redis.from_url(get_settings().redis_url, decode_responses=True)
        r.set(RESTART_BEAT_FLAG, datetime.now(timezone.utc).isoformat(), ex=600)
        return {"ok": True, "queued": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not queue the beat restart: {type(exc).__name__}")


@router.get("/task-status/{task_id}")
def get_task_status(task_id: str, _: CurrentUser):
    from app.tasks.celery_app import celery_app
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    return {
        "task_id": task_id,
        "status": result.status,
        "result": str(result.result) if result.ready() else None,
    }
