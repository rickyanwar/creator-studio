import io
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, status, UploadFile, File
from sqlalchemy.orm import joinedload

from app.api.deps import CurrentUser, DB
from app.config import get_settings
from app.schemas.fanpage import (
    FanpageOut, FanpageDetailOut, FanpageUpdate,
    FanpageSourceAdd, PreviewCaptionRequest, PreviewCaptionResponse,
    FanpageNewsSourceAdd, NewsSourceRef,
    PreviewNewsCopyRequest, PreviewNewsCopyResponse,
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

    from app.schemas.fanpage import IGSourceRef
    out = FanpageDetailOut.model_validate(fp)
    out.ig_sources = [
        IGSourceRef(
            id=s.id,
            ig_username=s.ig_username,
            album_image_indices=s.album_image_indices or [1],
            caption_tone=s.caption_tone,
            caption_language=s.caption_language,
            caption_max_length=s.caption_max_length,
            caption_hashtag_count=s.caption_hashtag_count,
            caption_cta_text=s.caption_cta_text,
            caption_custom_prompt=s.caption_custom_prompt,
        )
        for s in sources
    ]
    out.ig_source_usernames = [s.ig_username for s in sources]

    from app.models.fanpage_news_sources import FanpageNewsSource
    from app.models.news_sources import NewsSource
    news_links = db.query(FanpageNewsSource).filter_by(fanpage_id=fanpage_id, is_active=True).all()
    news_ids = [l.news_source_id for l in news_links]
    news = db.query(NewsSource).filter(NewsSource.id.in_(news_ids)).all() if news_ids else []
    out.news_sources = [
        NewsSourceRef(id=n.id, name=n.name, category_url=n.category_url) for n in news
    ]
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


@router.delete("/{fanpage_id}/sources/by-username/{username}")
def remove_ig_source_by_username(fanpage_id: int, username: str, db: DB, _: CurrentUser):
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource

    clean = username.lstrip("@").lower()
    source = db.query(IGSource).filter_by(ig_username=clean).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    link = db.query(FanpageSource).filter_by(fanpage_id=fanpage_id, ig_source_id=source.id).first()
    if not link:
        raise HTTPException(status_code=404, detail="Source link not found")

    link.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/{fanpage_id}/news-sources", status_code=status.HTTP_201_CREATED)
def add_news_source_link(fanpage_id: int, body: FanpageNewsSourceAdd, db: DB, _: CurrentUser):
    """Subscribe a fanpage to a news source (Mode 2)."""
    from app.models.target_fanpages import TargetFanpage
    from app.models.news_sources import NewsSource
    from app.models.fanpage_news_sources import FanpageNewsSource

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    source = db.query(NewsSource).filter_by(id=body.news_source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="News source not found")

    link = db.query(FanpageNewsSource).filter_by(
        fanpage_id=fanpage_id, news_source_id=source.id
    ).first()
    if link:
        link.is_active = True
    else:
        db.add(FanpageNewsSource(fanpage_id=fanpage_id, news_source_id=source.id, is_active=True))

    db.commit()
    return {"ok": True, "news_source_id": source.id, "name": source.name}


@router.delete("/{fanpage_id}/news-sources/{news_source_id}")
def remove_news_source_link(fanpage_id: int, news_source_id: int, db: DB, _: CurrentUser):
    from app.models.fanpage_news_sources import FanpageNewsSource

    link = db.query(FanpageNewsSource).filter_by(
        fanpage_id=fanpage_id, news_source_id=news_source_id
    ).first()
    if not link:
        raise HTTPException(status_code=404, detail="News source link not found")

    link.is_active = False
    db.commit()
    return {"ok": True}


@router.post("/{fanpage_id}/preview-news-copy", response_model=PreviewNewsCopyResponse)
def preview_news_copy(fanpage_id: int, body: PreviewNewsCopyRequest, db: DB, _: CurrentUser):
    """Preview the Mode 2 copywriter output for pasted article text."""
    from types import SimpleNamespace
    from app.models.target_fanpages import TargetFanpage
    from app.services.news_copywriter import generate_news_copy

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    article = SimpleNamespace(
        scraped_title=body.title,
        scraped_content=body.content,
        news_source=SimpleNamespace(name=body.source_name) if body.source_name else None,
    )
    try:
        copy = generate_news_copy(fp, article, force_provider=body.provider)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {exc}")

    return PreviewNewsCopyResponse(title=copy.title, caption=copy.caption, provider_used=copy.provider)


@router.post("/{fanpage_id}/preview-caption", response_model=PreviewCaptionResponse)
def preview_caption(fanpage_id: int, body: PreviewCaptionRequest, db: DB, _: CurrentUser):
    from app.models.target_fanpages import TargetFanpage
    from app.services.ai_caption import build_caption_prompt, generate_caption

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    # Preview honours the source's own caption criteria when it exists.
    from app.models.ig_sources import IGSource
    src = db.query(IGSource).filter_by(ig_username=body.source_username.lstrip("@").strip()).first()

    prompt = build_caption_prompt(
        fanpage=fp,
        source_username=body.source_username,
        original_caption=body.original_caption,
        source=src,
    )
    try:
        caption, provider = generate_caption(prompt, force_provider=body.provider)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {exc}")

    return PreviewCaptionResponse(caption=caption, provider_used=provider)


@router.post("/{fanpage_id}/watermark-image")
async def upload_watermark_image(fanpage_id: int, db: DB, _: CurrentUser, file: UploadFile = File(...)):
    """Upload a logo watermark for the fanpage's designs (stored as PNG to keep
    transparency). Overrides the text watermark on every rendered design."""
    from PIL import Image
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")

    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a readable image")

    s = get_settings()
    dest_dir = Path(s.storage_base_path) / "watermarks"
    dest_dir.mkdir(parents=True, exist_ok=True)
    filename = f"fp{fanpage_id}_{uuid.uuid4().hex[:8]}.png"
    img.save(dest_dir / filename, format="PNG")

    fp.watermark_image_url = f"{s.storage_base_url.rstrip('/')}/watermarks/{filename}"
    db.commit()
    return {"watermark_image_url": fp.watermark_image_url}


@router.delete("/{fanpage_id}/watermark-image")
def delete_watermark_image(fanpage_id: int, db: DB, _: CurrentUser):
    """Remove the logo watermark (designs fall back to the text watermark)."""
    from app.models.target_fanpages import TargetFanpage

    fp = db.query(TargetFanpage).filter_by(id=fanpage_id).first()
    if not fp:
        raise HTTPException(status_code=404, detail="Fanpage not found")
    fp.watermark_image_url = None
    db.commit()
    return {"ok": True}
