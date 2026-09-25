"""Mode 7 (YouTube clips) endpoints: sources, the clip-idea queue, and the
per-fanpage video status list. Fanpage-level settings (yt_clip_*) go through
the normal PUT /fanpages/{id}."""

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser, DB
from app.schemas.fanpage import (
    YtClipIdeaRef, YtClipIdeaUpdate, YtClipSourceAdd, YtClipSourceRef, YtClipSourceUpdate, YtVideoRef,
)

router = APIRouter(prefix="/fanpages", tags=["yt-clips"])

_PAGE_SIZE = 20


def _fanpage_or_404(db, fanpage_id: int):
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    return fp


def _source_or_404(db, fanpage_id: int, source_id: int):
    from app.models.yt_clip_sources import YtClipSource

    src = db.query(YtClipSource).filter_by(id=source_id, fanpage_id=fanpage_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    return src


def _idea_ref(idea) -> YtClipIdeaRef:
    return YtClipIdeaRef(
        id=idea.id, yt_video_row_id=idea.yt_video_row_id, video_id=idea.video_id,
        start_s=idea.start_s, end_s=idea.end_s, title=idea.title, description=idea.description,
        hook_text=idea.hook_text, virality_score=idea.virality_score,
        transcript_excerpt=idea.transcript_excerpt, status=idea.status, preview_url=idea.preview_url,
        video_title=idea.yt_video.title if idea.yt_video else None,
        created_at=idea.created_at, used_at=idea.used_at,
    )


# ── sources ──────────────────────────────────────────────────────────────────

@router.post("/{fanpage_id}/yt-clip-sources", response_model=YtClipSourceRef)
def add_yt_clip_source(fanpage_id: int, body: YtClipSourceAdd, db: DB, _: CurrentUser):
    """Paste any YouTube link — its kind (channel / playlist / video) is
    detected; a channel's UC… id is resolved once here and cached."""
    from app.models.yt_clip_sources import YtClipSource
    from app.services.youtube_source import classify_url, resolve_channel_id

    _fanpage_or_404(db, fanpage_id)
    try:
        parsed = classify_url(body.url)
        channel_id = parsed.channel_id or (resolve_channel_id(parsed.channel_path) if parsed.channel_path else None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't reach YouTube to resolve that channel: {exc}")

    existing = db.query(YtClipSource).filter_by(
        fanpage_id=fanpage_id, kind=parsed.kind, channel_id=channel_id,
        playlist_id=parsed.playlist_id, video_id=parsed.video_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="This fanpage already has that source.")
    src = YtClipSource(
        fanpage_id=fanpage_id, url=body.url.strip(), kind=parsed.kind, channel_id=channel_id,
        playlist_id=parsed.playlist_id, video_id=parsed.video_id,
        label=(body.label or "").strip() or None, direction=(body.direction or "").strip() or None,
    )
    db.add(src)
    db.commit()
    db.refresh(src)
    return YtClipSourceRef.model_validate(src)


@router.put("/{fanpage_id}/yt-clip-sources/{source_id}", response_model=YtClipSourceRef)
def update_yt_clip_source(fanpage_id: int, source_id: int, body: YtClipSourceUpdate, db: DB, _: CurrentUser):
    src = _source_or_404(db, fanpage_id, source_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(src, field, (value.strip() or None) if isinstance(value, str) else value)
    db.commit()
    db.refresh(src)
    return YtClipSourceRef.model_validate(src)


@router.delete("/{fanpage_id}/yt-clip-sources/{source_id}")
def delete_yt_clip_source(fanpage_id: int, source_id: int, db: DB, _: CurrentUser):
    db.delete(_source_or_404(db, fanpage_id, source_id))
    db.commit()
    return {"ok": True}


@router.post("/{fanpage_id}/yt-clip-sources/{source_id}/check")
def check_yt_clip_source(fanpage_id: int, source_id: int, db: DB, _: CurrentUser):
    """Look for new videos on this source right now instead of waiting for
    the next 30-minute tick."""
    from app.tasks.yt_clip import discover_source

    fp = _fanpage_or_404(db, fanpage_id)
    src = _source_or_404(db, fanpage_id, source_id)
    try:
        added = discover_source(db, fp, src)
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=f"Couldn't read that source: {exc}")
    return {"ok": True, "new_videos": added}


# ── clip ideas ───────────────────────────────────────────────────────────────

@router.get("/{fanpage_id}/yt-clip-ideas")
def list_yt_clip_ideas(fanpage_id: int, db: DB, _: CurrentUser, status: str = "pending", offset: int = 0):
    from app.models.yt_clip_ideas import YtClipIdea
    from app.models.yt_videos import YtVideo

    _fanpage_or_404(db, fanpage_id)
    rows = (
        db.query(YtClipIdea)
        .join(YtVideo, YtVideo.id == YtClipIdea.yt_video_row_id)
        .filter(YtClipIdea.fanpage_id == fanpage_id, YtClipIdea.status == status)
        .order_by(YtVideo.published_at.desc().nullslast(), YtClipIdea.virality_score.desc(), YtClipIdea.id.asc())
        .offset(max(0, offset)).limit(_PAGE_SIZE + 1).all()
    )
    return {"items": [_idea_ref(i) for i in rows[:_PAGE_SIZE]], "has_more": len(rows) > _PAGE_SIZE}


@router.put("/{fanpage_id}/yt-clip-ideas/{idea_id}", response_model=YtClipIdeaRef)
def update_yt_clip_idea(fanpage_id: int, idea_id: int, body: YtClipIdeaUpdate, db: DB, _: CurrentUser):
    from app.models.yt_clip_ideas import YtClipIdea

    idea = db.query(YtClipIdea).filter_by(id=idea_id, fanpage_id=fanpage_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(idea, field, " ".join(value.split()))
    db.commit()
    db.refresh(idea)
    return _idea_ref(idea)


@router.delete("/{fanpage_id}/yt-clip-ideas/{idea_id}")
def delete_yt_clip_idea(fanpage_id: int, idea_id: int, db: DB, _: CurrentUser):
    """Safe even while a job made from it renders — the job keeps its own
    copy of the clip range (FK is ON DELETE SET NULL)."""
    from app.models.yt_clip_ideas import YtClipIdea

    idea = db.query(YtClipIdea).filter_by(id=idea_id, fanpage_id=fanpage_id).first()
    if not idea:
        raise HTTPException(status_code=404, detail="Idea not found")
    db.delete(idea)
    db.commit()
    return {"ok": True}


# ── videos ───────────────────────────────────────────────────────────────────

@router.get("/{fanpage_id}/yt-videos")
def list_yt_videos(fanpage_id: int, db: DB, _: CurrentUser, offset: int = 0):
    """Every video discovered for this fanpage, newest first, with its
    status (analysed / skipped + why / failed + last error)."""
    from app.models.yt_videos import YtVideo

    _fanpage_or_404(db, fanpage_id)
    rows = (
        db.query(YtVideo).filter_by(fanpage_id=fanpage_id)
        .order_by(YtVideo.created_at.desc(), YtVideo.id.desc())
        .offset(max(0, offset)).limit(_PAGE_SIZE + 1).all()
    )
    return {"items": [YtVideoRef.model_validate(v) for v in rows[:_PAGE_SIZE]], "has_more": len(rows) > _PAGE_SIZE}


@router.post("/{fanpage_id}/yt-videos/{row_id}/retry", response_model=YtVideoRef)
def retry_yt_video(fanpage_id: int, row_id: int, db: DB, _: CurrentUser):
    """Put a skipped/failed video back in line for analysis."""
    from app.models.yt_videos import YtVideo

    video = db.query(YtVideo).filter_by(id=row_id, fanpage_id=fanpage_id).first()
    if not video:
        raise HTTPException(status_code=404, detail="Video not found")
    if video.status not in ("skipped", "failed"):
        raise HTTPException(status_code=409, detail=f"Video is {video.status}, not skipped/failed.")
    video.status, video.attempt_count, video.skip_reason, video.last_error = "discovered", 0, None, None
    db.commit()
    db.refresh(video)
    return YtVideoRef.model_validate(video)
