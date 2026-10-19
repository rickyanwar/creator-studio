from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from enum import Enum

from app.api.deps import CurrentUser, DB
from app.config import get_settings

router = APIRouter(prefix="/gallery", tags=["gallery"])
settings = get_settings()

_VALID_ENGINES = ("bing", "google", "9router", "pinterest")


class KeywordBody(BaseModel):
    keyword: str
    niche: Optional[str] = None
    is_active: Optional[bool] = True
    max_images: Optional[int] = 50
    max_pages: Optional[int] = 10
    min_width: Optional[int] = 200
    min_height: Optional[int] = 200
    source_engine: Optional[str] = "9router"
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

    @field_validator("max_pages")
    @classmethod
    def validate_max_pages(cls, v):
        if v is not None and not (1 <= v <= 50):
            raise ValueError("max_pages must be between 1 and 50")
        return v

    @field_validator("min_width", "min_height")
    @classmethod
    def validate_min_size(cls, v):
        if v is not None and v < 50:
            raise ValueError("minimum size must be at least 50")
        return v


class UpdateKeywordBody(KeywordBody):
    keyword: Optional[str] = None


def _serialize_keyword(kw, image_count: int = 0):
    return {
        "id": kw.id,
        "keyword": kw.keyword,
        "niche": kw.niche,
        "is_active": kw.is_active,
        "max_images": kw.max_images,
        "max_pages": kw.max_pages,
        "min_width": kw.min_width,
        "min_height": kw.min_height,
        "source_engine": kw.source_engine,
        "license_filter": kw.license_filter,
        "last_downloaded_at": kw.last_downloaded_at.replace(tzinfo=timezone.utc).isoformat() if kw.last_downloaded_at else None,
        "last_download_error": kw.last_download_error,
        "image_count": image_count,
        # Detected race/match/fight date — app.services.event_calendar via
        # gallery_downloader.refresh_keyword_event_dates, never entered
        # manually. Exposed here so Settings can show it read-only; the
        # per-niche press/practice/race WINDOW length around this date
        # (e.g. F1 opens 2 days before, MotoGP/UFC 3) lives in
        # gallery_downloader._event_window_days_before, not duplicated here.
        "next_event_date": kw.next_event_date.isoformat() if kw.next_event_date else None,
    }


def _serialize_image(img):
    return {
        "id": img.id,
        "keyword": img.keyword,
        "extra_keywords": img.extra_keywords or [],
        "source_image_url": img.source_image_url,
        "public_url": img.public_url,
        "width": img.width,
        "height": img.height,
        "source_engine": img.source_engine,
        "license_info": img.license_info,
        "is_used": img.is_used,
        "downloaded_at": img.downloaded_at.replace(tzinfo=timezone.utc).isoformat() if img.downloaded_at else None,
        "attributes": img.attributes or {},
        "labeled_at": img.labeled_at.replace(tzinfo=timezone.utc).isoformat() if img.labeled_at else None,
    }


# ── Keywords ─────────────────────────────────────────────────────────────────

def _normalize_niche(niche: Optional[str]) -> Optional[str]:
    if niche is None:
        return None
    niche = niche.strip()
    return niche or None


@router.get("/keywords")
def list_keywords(db: DB, _: CurrentUser, niche: Optional[str] = None):
    from app.models.gallery import GalleryKeyword, GalleryImage

    q = db.query(GalleryKeyword)
    if niche:
        q = q.filter(GalleryKeyword.niche == niche)
    keywords = q.order_by(GalleryKeyword.keyword).all()
    return [
        _serialize_keyword(kw, db.query(GalleryImage).filter_by(keyword=kw.keyword, is_deleted=False).count())
        for kw in keywords
    ]


@router.get("/niches")
def list_niches(db: DB, _: CurrentUser):
    """Distinct niches in use, for filter dropdowns / autocomplete."""
    from app.models.gallery import GalleryKeyword

    rows = (
        db.query(GalleryKeyword.niche)
        .filter(GalleryKeyword.niche.isnot(None))
        .distinct()
        .order_by(GalleryKeyword.niche)
        .all()
    )
    return [r[0] for r in rows]


@router.get("/fetch-stats")
def get_fetch_stats(db: DB, _: CurrentUser, days: int = Query(14, le=90)):
    """Daily paid 9Router web/fetch (jina-reader) call counts, by day and by
    context — see app.models.gallery_fetch_events. Built 2026-08-20 so
    actual Jina spend is queryable instead of estimated, following the
    gallery-download throttling work in gallery_downloader.py. `context`
    distinguishes the gallery keyword downloader from editorial_gate's
    fact-check search, event_calendar's date/time detection, and
    design_images.py's live single-image searches — all paid calls route
    through the same underlying function but aren't all "gallery spend"."""
    from sqlalchemy import case, func
    from app.models.gallery_fetch_events import GalleryFetchEvent

    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    day_col = func.date(GalleryFetchEvent.created_at)

    rows = (
        db.query(
            day_col.label("day"),
            GalleryFetchEvent.context,
            func.count().label("calls"),
            func.sum(case((GalleryFetchEvent.success.is_(False), 1), else_=0)).label("failures"),
        )
        .filter(GalleryFetchEvent.created_at >= cutoff)
        .group_by(day_col, GalleryFetchEvent.context)
        .order_by(day_col.desc())
        .all()
    )

    days_map: dict[str, dict] = {}
    for day, context, calls, failures in rows:
        day_str = day.isoformat() if hasattr(day, "isoformat") else str(day)
        entry = days_map.setdefault(day_str, {"date": day_str, "total": 0, "failures": 0, "by_context": {}})
        entry["total"] += calls
        entry["failures"] += failures or 0
        entry["by_context"][context] = calls

    return {"days": sorted(days_map.values(), key=lambda d: d["date"], reverse=True)}


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
        niche=_normalize_niche(body.niche),
        is_active=body.is_active if body.is_active is not None else True,
        max_images=body.max_images or 50,
        max_pages=body.max_pages or 10,
        min_width=body.min_width or 200,
        min_height=body.min_height or 200,
        source_engine=body.source_engine or "9router",
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
    if body.niche is not None:
        kw.niche = _normalize_niche(body.niche)
    if body.is_active is not None:
        kw.is_active = body.is_active
    if body.max_images is not None:
        kw.max_images = body.max_images
    if body.max_pages is not None:
        kw.max_pages = body.max_pages
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


class BulkKeywordItem(KeywordBody):
    """Same shape as KeywordBody — used for both bulk import and bulk edit.
    Matched against existing rows by `keyword` (case-insensitive): existing →
    updated in place, new → created. Nothing is deleted by an import."""
    pass


@router.post("/keywords/bulk-import")
def bulk_import_keywords(items: list[BulkKeywordItem], db: DB, _: CurrentUser):
    """Upsert many keywords at once from a pasted/uploaded JSON array — the
    bulk-edit and bulk-import flows are the same operation: edit the exported
    JSON (existing keywords keep their spot) or add brand-new entries, then
    submit the whole array back here."""
    from app.models.gallery import GalleryKeyword

    if not items:
        raise HTTPException(status_code=400, detail="No keywords in payload")

    created = 0
    updated = 0
    errors: list[str] = []

    for i, body in enumerate(items):
        keyword = (body.keyword or "").strip().lower()
        if not keyword:
            errors.append(f"item {i}: keyword is required")
            continue

        kw = db.query(GalleryKeyword).filter_by(keyword=keyword).first()
        if kw is None:
            kw = GalleryKeyword(keyword=keyword)
            db.add(kw)
            created += 1
        else:
            updated += 1

        kw.niche = _normalize_niche(body.niche)
        kw.is_active = body.is_active if body.is_active is not None else True
        kw.max_images = body.max_images or 50
        kw.max_pages = body.max_pages or 10
        kw.min_width = body.min_width or 200
        kw.min_height = body.min_height or 200
        kw.source_engine = body.source_engine or "9router"
        kw.license_filter = body.license_filter or "commercial,modify"

    db.commit()
    return {"created": created, "updated": updated, "errors": errors}


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


# ── AI closeup filter (manual, on-demand only — see scan_gallery_closeup_filter) ──

class AIFilterScanBody(BaseModel):
    criteria: str
    keyword: Optional[str] = None  # None/omitted = scan the whole gallery


class ImportLinksBody(BaseModel):
    links: str
    keyword: Optional[str] = None
    people: Optional[str] = None
    session: Optional[str] = None
    event_name: Optional[str] = None
    year: Optional[int] = Field(default=None, ge=1950, le=2100)
    note: Optional[str] = Field(default=None, max_length=300)


@router.post("/images/import-links")
def start_import_links(body: ImportLinksBody, db: DB, _: CurrentUser):
    from app.services.gallery_link_import import parse_links
    from app.tasks.gallery_link_import import import_gallery_links
    try:
        links = parse_links(body.links)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not links:
        raise HTTPException(status_code=400, detail="at least one link is required")
    hints = {key: value for key, value in {"people": [name.strip() for name in (body.people or "").split(",") if name.strip()], "session": body.session, "event_name": body.event_name, "year": body.year, "note": body.note}.items() if value not in (None, "", [])}
    task = import_gallery_links.delay(links, (body.keyword or "").strip().lower() or None, hints or None)
    return {"task_id": task.id}


@router.get("/images/import-links/{task_id}")
def get_import_links_status(task_id: str, _: CurrentUser):
    from celery.result import AsyncResult
    from app.tasks.celery_app import celery_app
    result = AsyncResult(task_id, app=celery_app)
    if not result.ready():
        return {"state": result.state}
    if result.failed():
        return {"state": "FAILURE"}
    return {"state": result.state, "results": (result.result or {}).get("results", [])}


@router.post("/ai-filter/scan")
def start_ai_filter_scan(body: AIFilterScanBody, db: DB, _: CurrentUser):
    """Kick off a read-only AI pass over the gallery (or one keyword, if
    given) against an admin-typed criteria. Nothing is deleted here — poll
    GET /ai-filter/scan/{task_id} for progress and the final candidate list
    (images that DON'T match), then let the admin review/uncheck before
    deleting via POST /images/bulk-delete."""
    from app.models.settings import Settings
    from app.tasks.gallery_downloader import scan_gallery_closeup_filter

    criteria = body.criteria.strip()
    if not criteria:
        raise HTTPException(status_code=400, detail="criteria is required")

    row = db.query(Settings).filter_by(id=1).first()
    if not row:
        row = Settings(id=1)
        db.add(row)
    row.gallery_ai_filter_last_criteria = criteria
    db.commit()

    keyword = (body.keyword or "").strip().lower() or None
    task = scan_gallery_closeup_filter.delay(criteria, keyword)
    return {"task_id": task.id}


@router.get("/ai-filter/scan/{task_id}")
def get_ai_filter_scan_status(task_id: str, _: CurrentUser):
    """Poll a scan's progress/result. `status` is a Celery state
    (PENDING/STARTED/PROGRESS/SUCCESS/FAILURE); `done`/`total` fill in while
    running; `candidates` ({id, public_url, keyword, confidence}[]) is only
    populated once SUCCESS."""
    from app.tasks.celery_app import celery_app
    from celery.result import AsyncResult

    result = AsyncResult(task_id, app=celery_app)
    if result.state == "PROGRESS":
        meta = result.info or {}
        return {"status": "PROGRESS", "done": meta.get("done", 0), "total": meta.get("total", 0), "candidates": None}
    if not result.ready():
        return {"status": result.status, "done": 0, "total": 0, "candidates": None}
    if result.failed():
        return {"status": "FAILURE", "done": 0, "total": 0, "candidates": None}
    data = result.result or {}
    return {
        "status": "SUCCESS",
        "done": data.get("done", 0),
        "total": data.get("total", 0),
        "candidates": data.get("candidates", []),
    }


# ── Images ───────────────────────────────────────────────────────────────────

@router.get("/images")
def list_images(
    db: DB,
    _: CurrentUser,
    keyword: Optional[str] = None,
    niche: Optional[str] = None,
    search: Optional[str] = None,
    only_unused: bool = False,
    limit: int = Query(60, le=200),
    offset: int = Query(0, ge=0),
):
    from sqlalchemy import or_, cast, String
    from app.models.gallery import GalleryImage, GalleryKeyword

    q = db.query(GalleryImage).filter(GalleryImage.is_deleted == False)
    if keyword:
        # Match the primary keyword or any extra tag — a photo with 2+ people
        # in frame is filed under one keyword but may be tagged with others.
        q = q.filter(or_(GalleryImage.keyword == keyword, GalleryImage.extra_keywords.any(keyword)))
    if niche:
        niche_keywords = db.query(GalleryKeyword.keyword).filter(GalleryKeyword.niche == niche)
        q = q.filter(GalleryImage.keyword.in_(niche_keywords))
    if search:
        # Free-text search box — substring match against the primary keyword
        # or any extra tag, unlike the exact-match `keyword` param above.
        pattern = f"%{search.strip()}%"
        q = q.filter(
            or_(
                GalleryImage.keyword.ilike(pattern),
                cast(GalleryImage.extra_keywords, String).ilike(pattern),
            )
        )
    if only_unused:
        q = q.filter(GalleryImage.is_used == False)

    total = q.count()
    images = q.order_by(GalleryImage.downloaded_at.desc()).offset(offset).limit(limit).all()
    return {"total": total, "images": [_serialize_image(i) for i in images]}


class ExtraKeywordsBody(BaseModel):
    extra_keywords: list[str]


class ShotType(str, Enum):
    car_on_track = "car_on_track"
    half_body = "half_body"
    face_closeup = "face_closeup"
    full_body = "full_body"
    portrait = "portrait"
    athlete_in_action = "athlete_in_action"
    pitlane = "pitlane"
    podium = "podium"
    car_static = "car_static"
    team_group = "team_group"
    vehicle_static = "vehicle_static"
    crowd = "crowd"
    other = "other"


class ManualLabelsBody(BaseModel):
    people: list[str] | None = Field(default=None, max_length=4)
    shot_type: ShotType | None = None
    session: str | None = Field(default=None, min_length=1)
    year: int | None = Field(default=None, ge=1950, le=2100)
    event_name: str | None = Field(default=None, min_length=1)
    archival_photo: bool | None = None
    people_count: int | None = Field(default=None, ge=0, le=20)
    label: str | None = None

    @field_validator("people")
    @classmethod
    def validate_people(cls, values):
        if values is not None and any(not 2 <= len(name.strip()) <= 60 for name in values):
            raise ValueError("people names must be 2-60 characters")
        return [name.strip() for name in values] if values is not None else values

    @field_validator("label")
    @classmethod
    def validate_label(cls, value):
        if value is not None and value not in {"face", "action", "other"}:
            raise ValueError("label must be face, action, or other")
        return value


@router.patch("/images/{image_id}/labels")
def update_image_labels(image_id: int, body: ManualLabelsBody, db: DB, _: CurrentUser, relabel: bool = False):
    from app.models.gallery import GalleryImage
    image = db.query(GalleryImage).filter_by(id=image_id, is_deleted=False).first()
    if not image:
        raise HTTPException(status_code=404, detail="Image not found")
    attributes = dict(image.attributes or {})
    manual_fields = set(attributes.get("manual_fields") or [])
    changes = body.model_dump(exclude_none=True)
    if "people" in changes:
        attributes["people"] = [{"name": name, "confidence": 1.0, "source": "manual"} for name in changes.pop("people")]
        attributes["keyword_mismatch"] = False
    event = dict(attributes.get("event") or {})
    for field in ("session", "year", "event_name"):
        if field in changes:
            event[field] = changes.pop(field)
            manual_fields.add(f"event.{field}")
    if event:
        attributes["event"] = event
    for field, value in changes.items():
        attributes[field] = value.value if isinstance(value, Enum) else value
    manual_fields.update(body.model_dump(exclude_none=True).keys())
    attributes["manual_fields"] = sorted(manual_fields)
    image.attributes = attributes
    if image.labeled_at is None:
        image.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(image)
    if relabel:
        from app.tasks.gallery_labels import label_gallery_image
        label_gallery_image.delay(image.id, force=True, relabel=True)
    return _serialize_image(image)


@router.patch("/images/{image_id}/keywords")
def update_image_keywords(image_id: int, body: ExtraKeywordsBody, db: DB, _: CurrentUser):
    """Tag an image with additional keywords — a photo can feature more than
    one person, so it can match under names besides its primary keyword."""
    from app.models.gallery import GalleryImage

    img = db.query(GalleryImage).filter_by(id=image_id, is_deleted=False).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    seen = set()
    cleaned = []
    for kw in body.extra_keywords:
        kw = kw.strip().lower()
        if not kw or kw == img.keyword or kw in seen:
            continue
        seen.add(kw)
        cleaned.append(kw)

    img.extra_keywords = cleaned
    db.commit()
    db.refresh(img)
    return _serialize_image(img)


@router.post("/images/upload")
async def upload_image(
    db: DB,
    _: CurrentUser,
    file: UploadFile = File(...),
    keyword: str = Form(...),
    people: str = Form(""),
    session: str = Form(""),
    event_name: str = Form(""),
    year: Optional[int] = Form(None),
    note: str = Form(""),
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
    owner_hints = {key: value for key, value in {"people": [name.strip() for name in people.split(",") if name.strip()], "session": session.strip() or None, "event_name": event_name.strip() or None, "year": year, "note": note.strip()[:300] or None}.items() if value not in (None, "", [])}
    if owner_hints:
        image.attributes = {"owner_hints": owner_hints, "manual_fields": sorted((["people"] if owner_hints.get("people") else []) + [f"event.{field}" for field in ("session", "event_name", "year") if owner_hints.get(field)])}
    db.add(image)
    db.commit()
    db.refresh(image)
    from app.tasks.gallery_labels import label_gallery_image
    label_gallery_image.delay(image.id, force=True)
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
    """Soft delete: the file is removed from disk, but the row (and its
    source_image_url) is kept and flagged so the same photo is never
    re-downloaded by a future gallery run for this keyword."""
    from app.models.gallery import GalleryImage

    img = db.query(GalleryImage).filter_by(id=image_id, is_deleted=False).first()
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")

    Path(img.local_path).unlink(missing_ok=True)
    img.is_deleted = True
    img.deleted_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    return {"ok": True}


class BulkDeleteImagesBody(BaseModel):
    image_ids: list[int]


@router.post("/images/bulk-delete")
def bulk_delete_images(body: BulkDeleteImagesBody, db: DB, _: CurrentUser):
    """Soft-delete several images at once — same sequence as delete_image,
    batched. Used to confirm the AI filter's reviewed candidate list (see
    start_ai_filter_scan), but works for any id list."""
    from app.models.gallery import GalleryImage

    if not body.image_ids:
        raise HTTPException(status_code=400, detail="image_ids is required")

    imgs = (
        db.query(GalleryImage)
        .filter(GalleryImage.id.in_(body.image_ids), GalleryImage.is_deleted == False)
        .all()
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for img in imgs:
        Path(img.local_path).unlink(missing_ok=True)
        img.is_deleted = True
        img.deleted_at = now
    db.commit()
    return {"deleted": len(imgs)}
