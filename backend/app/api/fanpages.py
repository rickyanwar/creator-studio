from fastapi import APIRouter, HTTPException, status
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DB
from app.schemas.fanpage import (
    FanpageOut, FanpageDetailOut, FanpageUpdate,
    FanpageSourceAdd, PreviewCaptionRequest, PreviewCaptionResponse,
)

router = APIRouter(prefix="/fanpages", tags=["fanpages"])


@router.get("", response_model=list[FanpageOut])
def list_fanpages(db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    return db.query(TargetFanpage).order_by(TargetFanpage.name).all()


@router.get("/{fanpage_id}", response_model=FanpageDetailOut)
def get_fanpage(fanpage_id: int, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.models.fanpage_sources import FanpageSource
    from app.models.ig_sources import IGSource

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    links = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, is_active=True).all()
    source_ids = [l.ig_source_id for l in links]
    sources = db.query(IGSource).filter(IGSource.id.in_(source_ids)).all() if source_ids else []

    out = FanpageDetailOut.model_validate(fp)
    out.ig_source_usernames = [s.ig_username for s in sources]
    return out


@router.put("/{fanpage_id}", response_model=FanpageOut)
def update_fanpage(fanpage_id: int, body: FanpageUpdate, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(fp, field, value)

    db.commit()
    db.refresh(fp)
    return fp


@router.post("/{fanpage_id}/sources", status_code=status.HTTP_201_CREATED)
def add_ig_source(fanpage_id: int, body: FanpageSourceAdd, db: DB, _: CurrentUser):
    """Add an IG username as a source for a fanpage. Auto-creates IGSource if new."""
    from app.models.target_fanpages import TargetFanpage
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource
    from app.services.ig_session_manager import get_least_used_burner

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    username = body.ig_username.lstrip("@").lower()

    # Upsert IGSource
    source = db.query(IGSource).filter_by(ig_username=username).first()
    if not source:
        burner = get_least_used_burner(db)
        source = IGSource(
            ig_username=username,
            burner_account_id=burner.id if burner else None,
            is_active=True,
        )
        db.add(source)
        db.flush()

    # Upsert FanpageSource link
    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=source.id).first()
    if link:
        link.is_active = True
    else:
        link = FanpageSource(fanpage_id=fanpage_id, ig_source_id=source.id, is_active=True)
        db.add(link)

    db.commit()
    return {"ok": True, "ig_source_id": source.id, "ig_username": source.ig_username}


@router.delete("/{fanpage_id}/sources/{ig_source_id}")
def remove_ig_source(fanpage_id: int, ig_source_id: int, db: DB, _: CurrentUser):
    from app.models.fanpage_sources import FanpageSource

    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=ig_source_id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Source link not found")

    link.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/{fanpage_id}/preview-caption", response_model=PreviewCaptionResponse)
def preview_caption(fanpage_id: int, body: PreviewCaptionRequest, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.services.ai_caption import build_caption_prompt, generate_caption

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    prompt = build_caption_prompt(
        fanpage=fp,
        source_username=body.source_username,
        original_caption=body.original_caption,
    )
    try:
        caption, provider = generate_caption(prompt, force_provider=body.provider)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {exc}")

    return PreviewCaptionResponse(caption=caption, provider_used=provider)
