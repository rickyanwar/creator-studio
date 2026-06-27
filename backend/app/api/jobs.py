"""Manual job triggers — for testing and admin actions."""

import subprocess
from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/jobs", tags=["jobs"])


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
    """Restart the Celery beat scheduler container."""
    try:
        result = subprocess.run(
            ["docker", "compose", "restart", "beat"],
            capture_output=True, text=True, timeout=30,
            cwd="/opt/studio",
        )
        if result.returncode != 0:
            raise HTTPException(status_code=500, detail=result.stderr.strip() or "Restart failed")
        return {"ok": True}
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="Restart timed out")
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="docker not found on server")


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
