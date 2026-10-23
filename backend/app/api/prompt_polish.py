"""Prompt polish read endpoints for the admin UI."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CurrentUser, DB
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_prepare import valid_prepared
from app.services.prompt_polish_health import snapshot

router = APIRouter(prefix="/prompt-polish", tags=["prompt-polish"])


@router.get("/health")
def get_prompt_polish_health(db: DB, _: CurrentUser):
    return snapshot(db)


@router.get("/ideas/{idea_id}")
def get_prompt_polish_idea(idea_id: int, db: DB, _: CurrentUser):
    idea = db.query(VideoIdea).filter(VideoIdea.id == idea_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == idea.fanpage_id).first()
    if fanpage is None or not getattr(fanpage, "ai_video_prompt_polish_enabled", False):
        state = "off"
    elif valid_prepared(idea) is not None:
        state = "polished"
    elif getattr(idea, "status", None) == "failed" and (getattr(idea, "error", "") or "").startswith("Argon prompt polish failed"):
        state = "failed"
    else:
        state = "waiting"

    prep = valid_prepared(idea) or {}
    raw_clips = prep.get("clips") or []
    clips = [
        {
            "index": c.get("index"),
            "mode": c.get("mode"),
            "prompt": c.get("prompt"),
            "prompt_original": c.get("prompt_original"),
        }
        for c in raw_clips
        if isinstance(c, dict)
    ]

    return {
        "idea_id": idea.id,
        "state": state,
        "prepared_at": idea.prepared_at.isoformat() if idea.prepared_at else None,
        "polish_attempts": idea.polish_attempts or 0,
        "polish_error": idea.polish_error,
        "changes": prep.get("changes") or [],
        "voice_sentence": prep.get("voice_sentence") or "",
        "ambience_sentence": prep.get("ambience_sentence") or "",
        "clips": clips,
    }


@router.get("/jobs/{job_id}")
def get_prompt_polish_job(job_id: int, db: DB, _: CurrentUser):
    job = db.query(VideoJob).filter(VideoJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    job_clips = job.clips or []
    if isinstance(job_clips, str):
        try:
            job_clips = json.loads(job_clips)
        except Exception:
            job_clips = []

    polished = any(bool(c.get("prompt_original")) for c in job_clips if isinstance(c, dict))
    clips = [
        {
            "index": c.get("index"),
            "mode": c.get("mode"),
            "prompt": c.get("prompt"),
            "prompt_original": c.get("prompt_original") if c.get("prompt_original") is not None else None,
        }
        for c in job_clips
        if isinstance(c, dict)
    ]

    changes = []
    if job.idea_id:
        idea = db.query(VideoIdea).filter(VideoIdea.id == job.idea_id).first()
        if idea and isinstance(idea.prepared, dict):
            changes = idea.prepared.get("changes") or []

    return {
        "job_id": job.id,
        "polished": polished,
        "clips": clips,
        "changes": changes,
    }


@router.get("/ideas-state")
def get_prompt_polish_ideas_state(
    ids: Optional[str] = Query(None),
    db: DB = None,
    _: CurrentUser = None,
):
    if not ids:
        return {}

    id_list = []
    for part in ids.split(","):
        part = part.strip()
        if part.isdigit():
            id_list.append(int(part))
            if len(id_list) >= 200:
                break

    if not id_list:
        return {}

    ideas = db.query(VideoIdea).filter(VideoIdea.id.in_(id_list)).all()
    if not ideas:
        return {}

    fp_ids = {idea.fanpage_id for idea in ideas}
    fanpages = {
        fp.id: fp
        for fp in db.query(TargetFanpage).filter(TargetFanpage.id.in_(fp_ids)).all()
    }

    result = {}
    for idea in ideas:
        fanpage = fanpages.get(idea.fanpage_id)
        if fanpage is None or not getattr(fanpage, "ai_video_prompt_polish_enabled", False):
            state = "off"
        elif valid_prepared(idea) is not None:
            state = "polished"
        elif getattr(idea, "status", None) == "failed" and (getattr(idea, "error", "") or "").startswith("Argon prompt polish failed"):
            state = "failed"
        else:
            state = "waiting"
        result[str(idea.id)] = state

    return result
