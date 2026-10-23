"""Prompt polish read endpoints for the admin UI."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc

from app.api.deps import CurrentUser, DB
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_prepare import valid_prepared
from app.services.prompt_polish_health import snapshot

router = APIRouter(prefix="/prompt-polish", tags=["prompt-polish"])


def _newest_job_for_idea(db, idea_id: int) -> VideoJob | None:
    """Return the most recently created VideoJob for a given idea_id, or None."""
    return (
        db.query(VideoJob)
        .filter(VideoJob.idea_id == idea_id)
        .order_by(desc(VideoJob.id))
        .first()
    )


def _job_clips_list(job: VideoJob) -> list:
    """Return job.clips as a list, parsing JSON strings if needed."""
    raw = job.clips or []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = []
    return raw if isinstance(raw, list) else []


def _job_is_polished(job: VideoJob) -> bool:
    """True when at least one clip in the job has a non-empty prompt_original."""
    return any(
        bool(c.get("prompt_original"))
        for c in _job_clips_list(job)
        if isinstance(c, dict)
    )


def _idea_state(idea, fanpage, newest_job: VideoJob | None) -> str:
    """
    Compute the polish state for an idea given its fanpage and its newest VideoJob.
    Job-based override: if a VideoJob exists and has polished clips → 'polished',
    regardless of the fingerprint / valid_prepared check.
    """
    if fanpage is None or not getattr(fanpage, "ai_video_prompt_polish_enabled", False):
        return "off"
    # Job-based check: a VideoJob with polished clips overrides fingerprint validation.
    if newest_job is not None and _job_is_polished(newest_job):
        return "polished"
    # Fingerprint-valid prepared payload (ideas not yet turned into a job).
    if valid_prepared(idea) is not None:
        return "polished"
    if getattr(idea, "status", None) == "failed" and (getattr(idea, "error", "") or "").startswith("Argon prompt polish failed"):
        return "failed"
    return "waiting"


@router.get("/health")
def get_prompt_polish_health(db: DB, _: CurrentUser):
    return snapshot(db)


@router.get("/ideas/{idea_id}")
def get_prompt_polish_idea(idea_id: int, db: DB, _: CurrentUser):
    idea = db.query(VideoIdea).filter(VideoIdea.id == idea_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")

    fanpage = db.query(TargetFanpage).filter(TargetFanpage.id == idea.fanpage_id).first()
    newest_job = _newest_job_for_idea(db, idea.id)
    state = _idea_state(idea, fanpage, newest_job)

    # Clips: prefer job clips when the job is the reason we're polished.
    if newest_job is not None and _job_is_polished(newest_job):
        raw_clips = _job_clips_list(newest_job)
    else:
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

    # changes: from idea.prepared when present, regardless of fingerprint validity.
    prep_any = idea.prepared if isinstance(idea.prepared, dict) else {}
    changes = prep_any.get("changes") or []

    return {
        "idea_id": idea.id,
        "state": state,
        "prepared_at": idea.prepared_at.isoformat() if idea.prepared_at else None,
        "polish_attempts": idea.polish_attempts or 0,
        "polish_error": idea.polish_error,
        "changes": changes,
        "voice_sentence": prep_any.get("voice_sentence") or "",
        "ambience_sentence": prep_any.get("ambience_sentence") or "",
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

    # Load newest job per idea in ONE query to avoid N+1.
    # Subquery: for each idea_id, the max job id.
    from sqlalchemy import func as sqlfunc
    subq = (
        db.query(
            VideoJob.idea_id,
            sqlfunc.max(VideoJob.id).label("max_id"),
        )
        .filter(VideoJob.idea_id.in_(id_list))
        .group_by(VideoJob.idea_id)
        .subquery()
    )
    newest_jobs_rows = (
        db.query(VideoJob)
        .join(subq, VideoJob.id == subq.c.max_id)
        .all()
    )
    newest_jobs: dict[int, VideoJob] = {j.idea_id: j for j in newest_jobs_rows}

    result = {}
    for idea in ideas:
        fanpage = fanpages.get(idea.fanpage_id)
        newest_job = newest_jobs.get(idea.id)
        result[str(idea.id)] = _idea_state(idea, fanpage, newest_job)

    return result
