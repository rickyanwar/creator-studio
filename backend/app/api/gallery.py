from datetime import timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, field_validator

from app.api.deps import CurrentUser, DB
from app.config import get_settings

router = APIRouter(prefix="/gallery", tags=["gallery"])
settings = get_settings()

_VALID_ENGINES = ("bing", "google")


class KeywordBody(BaseModel):
    keyword: str
    is_active: Optional[bool] = True
    max_images: Optional[int] = 50
    min_width: Optional[int] = 500
    min_height: Optional[int] = 500
    source_engine: Optional[str] = "bing"
    license_filter: Optional[str] = "commercial,modify"

    @field_validator("source_engine")
    @classmethod
    def validate_engine(cls, v):
        if v not in (None, *_VALID_ENGINES):
            raise ValueError(f"source_engine must be one of {_VALID_ENGINES}")
        return v

    @field_validator("max_images")
    @classmethod
    def validate_max(cls, v):
        if v is not None and not (1 <= v <= 200):
            raise ValueError("max_images must be between 1 and 200")
        return v

    @field_validator("min_width", "min_height")
    @classmethod
    def validate_min_size(cls, v):
        if v is not None and v < 100:
            raise ValueError("minimum size must be at least 100")
        return v


class UpdateKeywordBody(KeywordBody):
    keyword: Optional[str] = None


def _serialize_keyword(kw, image_count: int = 0):
    return {
        "id": kw.id,
        "keyword": kw.keyword,
        "is_active": kw.is_active,
        "max_images": kw.max_images,
        "min_width": kw.min_width,
        "min_height": kw.min_height,
        "source_engine": kw.source_engine,
        "license_filter": kw.license_filter,
        "last_downloaded_at": kw.last_downloaded_at.replace(tzinfo=timezone.utc).isoformat() if kw.last_downloaded_at else None,
        "last_download_error": kw.last_download_error,
        "image_count": image_count,
    }


def _serialize_image(img):
    return {
        "id": img.id,
        "keyword": img.keyword,
        "source_image_url": img.source_image_url,
        "public_url": img.public_url,
        "width": img.width,
        "height": img.height,
        "source_engine": img.source_engine,
        "license_info": img.license_info,
        "is_used": img.is_used,
        "downloaded_at": img.downloaded_at.replace(tzinfo=timezone.utc).isoformat() if img.downloaded_at else None,
    }


# ── Keywords ─────────────────────────────────────────────────────────────────

@router.get("/keywords")
def list_keywords(db: DB, _: CurrentUser):
    from app.models.gallery import GalleryKeyword, GalleryImage

    keywords = db.query(GalleryKeyword).order_by(GalleryKeyword.keyword).all()
    return [
        _serialize_keyword(kw, db.query(GalleryImage).filter_by(keyword=kw.keyword).count())
        for kw in keywords
    ]


@router.post("/keywords")
def create_keyword(body: KeywordBody, db: DB, _: CurrentUser):
    from app.models.gallery import GalleryKeyword

    keyword = body.keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")
    if db.query(GalleryKeyword).filter_by(keyword=keyword).first():
        raise HTTPException(status_code=409, detail="keyword already exists")

    kw = GalleryKeyword(
        keyword=keyword,
        is_active=body.is_active if body.is_active is not None else True,
        max_images=body.max_images or 50,
        min_width=body.min_width or 500,
        min_height=body.min_height or 500,
        source_engine=body.source_engine or "bing",
        license_filter=body.license_filter or "commercial,modify",
    )
    db.add(kw)
    db.commit()
    db.refresh(kw)
    return _serialize_keyword(kw)


@router.put("/keywords/{keyword_id}")
def update_keyword(keyword_id: int, body: UpdateKeywordBody, db: DB, _: CurrentUser):
    from app.models.gallery import GalleryKeyword

    kw = db.query(GalleryKeyword).filter_by(id=keyword_id).first()
    if not kw:
        raise HTTPException(status_code=404, detail="Keyword not found")

    if body.keyword is not None:
        kw.keyword = body.keyword.strip().lower()
    if body.is_active is not None:
        kw.is_active = body.is_active
    if body.max_images is not None:
        kw.max_images = body.max_images
    if body.min_width is not None:
        kw.min_width = body.min_width
    if body.min_height is not None:
        kw.min_height = body.min_height
    if body.source_engine is not None:
        kw.source_engine = body.source_engine
    if body.license_filter is not None:
        kw.license_filter = body.license_filter

    db.commit()
    db.refresh(kw)
    return _serialize_keyword(kw)


@router.delete("/keywords/{keyword_id}")
def delete_keyword(keyword_id: int, db: DB, _: CurrentUser):
    """Delete a keyword config. Its already-downloaded images are kept."""
    from app.models.gallery import GalleryKeyword

    kw = db.query(GalleryKeyword).filter_by(id=keyword_id).first()
    if not kw:
        raise HTTPException(status_code=404, detail="Keyword not found")

    db.delete(kw)
    db.commit()
    return {"ok": True}


@router.post("/keywords/{keyword_id}/download-now")
def download_now(keyword_id: int, db: DB, _: CurrentUser):
    from app.models.gallery import GalleryKeyword
    from app.tasks.gallery_downloader import download_keyword

    kw = db.query(GalleryKeyword).filter_by(id=keyword_id).first()
    if not kw:
        raise HTTPException(status_code=404, detail="Keyword not found")

    download_keyword.delay(keyword_id)
    return {"ok": True, "message": f"Download queued for '{kw.keyword}'"}


# ── Images ───────────────────────────────────────────────────────────────────

@router.get("/images")
def list_images(
    db: DB,
    _: CurrentUser,
    keyword: Optional[str] = None,
    only_unused: bool = False,
    limit: int = Query(60, le=200),
    offset: int = Query(0, ge=0),
):
    from app.models.gallery import GalleryImage

    q = db.query(GalleryImage)
    if keyword:
        q = q.filter(GalleryImage.keyword == keyword)
    if only_unused:
        q = q.filter(GalleryImage.is_used == False)

    total = q.count()
    images = q.order_by(GalleryImage.downloaded_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "images": [_serialize_image(i) for i in images]}


@router.post("/images/upload")
async def upload_image(
    db: DB,
    _: CurrentUser,
    file: UploadFile = File(...),
    keyword: str = Form(...),
):
    """Manually upload an image into the gallery under a keyword."""
    from app.models.gallery import GalleryImage
    from app.services.image_downloader import keyword_slug, validate_and_store_upload

    keyword = keyword.strip().lower()
    if not keyword:
        raise HTTPException(status_code=400, detail="keyword is required")

    file_bytes = await file.read()
    slug = keyword_slug(keyword)
    dest_dir = Path(settings.storage_base_path) / "gallery" / slug

    try:
        item = validate_and_store_upload(file_bytes, dest_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception:
        raise HTTPException(status_code=400, detail="File is not a readable image")

    image = GalleryImage(
        keyword=keyword,
        source_image_url=item.source_url,
        local_path=item.local_path,
        public_url=f"{settings.storage_base_url.rstrip('/')}/gallery/{slug}/{item.filename}",
        width=item.width,
        height=item.height,
        source_engine="manual",
        license_info="manual-upload",
    )
    db.add(image)
    db.commit()
    db.refresh(image)
    return _serialize_image(image)


@router.get("/proxy")
def proxy_image(url: str, _: CurrentUser):
    """Serve a media image with API CORS headers so the designer canvas can
    read it without tainting (nginx media host may not send CORS headers).
    Local media URLs are read from disk; remote URLs (article hero images)
    are fetched on the fly."""
    import httpx
    from fastapi.responses import Response

    base_url = settings.storage_base_url.rstrip("/")
    if url.startswith(base_url):
        rel = url[len(base_url):].lstrip("/")
        path = (Path(settings.storage_base_path) / rel).resolve()
        if not str(path).startswith(str(Path(settings.storage_base_path).resolve())):
            raise HTTPException(status_code=400, detail="Invalid path")
        if not path.is_file():
            raise HTTPException(status_code=404, detail="File not found")
        media_type = "image/png" if path.suffix == ".png" else "image/jpeg"
        return Response(content=path.read_bytes(), media_type=media_type)

    if url.startswith(("http://", "https://")):
        try:
            resp = httpx.get(url, headers={"User-Agent": "Mozilla/5.0 (compatible; MediaBot/1.0)"}, timeout=30.0, follow_redirects=True)
            resp.raise_for_status()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Fetch failed: {exc}")
        return Response(content=resp.content, media_type=resp.headers.get("content-type", "image/jpeg"))

    raise HTTPException(status_code=400, detail="Unsupported URL")


@router.delete("/images/{image_id}")
def delete_image(image_id: int, db: DB, _: CurrentUser):
    from app.models.gallery import GalleryImage

    img = db.query(GalleryImage).filter_by(id=image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    Path(img.local_path).unlink(missing_ok=True)
    db.delete(img)
    db.commit()
    return {"ok": True}
