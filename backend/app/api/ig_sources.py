from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from datetime import timezone
from pydantic import BaseModel, field_validator

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/ig-sources", tags=["ig-sources"])


class AssignBurnerRequest(BaseModel):
    burner_id: Optional[int] = None


class UpdateIGSourceRequest(BaseModel):
    ig_username: Optional[str] = None
    is_active: Optional[bool] = None
    album_image_indices: Optional[List[int]] = None
    scraper_backend: Optional[str] = None  # "auto" | "instagrapi" | "viewer"
    niche: Optional[str] = None
    # Per-source caption criteria (None = leave unchanged; "" = clear → fanpage default)
    caption_tone: Optional[str] = None
    caption_language: Optional[str] = None
    caption_max_length: Optional[int] = None
    caption_hashtag_count: Optional[int] = None
    caption_cta_text: Optional[str] = None
    caption_custom_prompt: Optional[str] = None

    @field_validator("album_image_indices")
    @classmethod
    def validate_indices(cls, v):
        if v is None:
            return v
        if not v:
            raise ValueError("album_image_indices cannot be empty")
        for i in v:
            if i < 1 or i > 10:
                raise ValueError("Each index must be between 1 and 10")
        return sorted(set(v))


@router.get("")
def list_ig_sources(
    db: DB,
    _: CurrentUser,
    orphan_only: bool = Query(False, description="Only show sources with no active fanpage links"),
):
    from collections import defaultdict
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource
    from app.models.target_fanpages import TargetFanpage

    sources = db.query(IGSource).order_by(IGSource.ig_username).all()

    active_links = (
        db.query(FanpageSource.ig_source_id, TargetFanpage)
        .outerjoin(TargetFanpage, FanpageSource.fanpage_id == TargetFanpage.id)
        .filter(FanpageSource.is_active == True)
        .all()
    )
    source_fanpages = defaultdict(list)
    source_active_counts = defaultdict(int)
    for src_id, fp in active_links:
        source_active_counts[src_id] += 1
        if fp is not None:
            source_fanpages[src_id].append(fp)

    result = []
    for s in sources:
        active_links_count = source_active_counts[s.id]
        if orphan_only and active_links_count > 0:
            continue

        linked_fps = source_fanpages.get(s.id, [])
        all_niches = set()
        has_fp_with_niches = False
        for fp in linked_fps:
            niches = fp.mode2_gallery_niches or []
            if isinstance(niches, str):
                import json
                try:
                    niches = json.loads(niches)
                except Exception:
                    niches = [n.strip('"{}') for n in niches.strip('{}').split(',') if n.strip('"{}')]
            elif isinstance(niches, list) and len(niches) > 1 and niches[0] == "[" and niches[-1] == "]":
                import json
                try:
                    niches = json.loads("".join(niches))
                except Exception:
                    pass
            if niches:
                has_fp_with_niches = True
                for n in niches:
                    if n:
                        all_niches.add(n)
        fanpage_niches = sorted(all_niches)

        niche_mismatch = False
        if s.niche and has_fp_with_niches:
            if s.niche not in all_niches:
                if s.niche == "Motorsport" and ("F1" in all_niches or "MotoGP" in all_niches):
                    niche_mismatch = False
                else:
                    niche_mismatch = True

        result.append({
            "id": s.id,
            "ig_username": s.ig_username,
            "ig_user_id": s.ig_user_id,
            "burner_id": None,
            "burner_username": None,
            "burner_status": None,
            "is_active": s.is_active,
            "last_checked_at": s.last_checked_at.replace(tzinfo=timezone.utc).isoformat() if s.last_checked_at else None,
            "last_seen_post_id": s.last_seen_post_id,
            "active_fanpage_count": active_links_count,
            "album_image_indices": s.album_image_indices or [1],
            "scraper_backend": (s.scraper_backend.value if s.scraper_backend else "auto"),
            "last_crawl_error": s.last_crawl_error,
            "caption_tone": s.caption_tone,
            "caption_language": s.caption_language,
            "caption_max_length": s.caption_max_length,
            "caption_hashtag_count": s.caption_hashtag_count,
            "caption_cta_text": s.caption_cta_text,
            "caption_custom_prompt": s.caption_custom_prompt,
            "niche": s.niche,
            "fanpage_niches": fanpage_niches,
            "niche_mismatch": niche_mismatch,
        })

    return result


@router.patch("/{source_id}/assign-burner")
def assign_burner(source_id: int, body: AssignBurnerRequest, db: DB, _: CurrentUser):
    # Deprecated endpoint: burner assignment is disabled
    return {"ok": True}


@router.patch("/{source_id}")
def update_ig_source(source_id: int, body: UpdateIGSourceRequest, db: DB, _: CurrentUser):
    from app.models.ig_sources import IGSource

    source = db.query(IGSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if body.ig_username is not None:
        username = body.ig_username.lstrip("@").strip()
        if not username:
            raise HTTPException(status_code=400, detail="Username cannot be empty")
        source.ig_username = username

    if body.is_active is not None:
        source.is_active = body.is_active

    if body.album_image_indices is not None:
        source.album_image_indices = body.album_image_indices

    if body.scraper_backend is not None:
        from app.models.ig_sources import ScraperBackend
        # Remap legacy "flashapi" input to "viewer" (treated identically at runtime)
        backend_val = "viewer" if body.scraper_backend == "flashapi" else body.scraper_backend
        try:
            source.scraper_backend = ScraperBackend(backend_val)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid scraper_backend: must be auto, instagrapi, or viewer")

    provided = body.model_fields_set
    if "niche" in provided:
        if body.niche in ("", None):
            source.niche = None
        else:
            cleaned = body.niche.strip()[:64]
            source.niche = cleaned if cleaned else None

    # Caption criteria: apply only fields present in the request; "" clears back
    # to the fanpage default (stored as NULL).
    for field in ("caption_tone", "caption_language", "caption_max_length",
                  "caption_hashtag_count", "caption_cta_text", "caption_custom_prompt"):
        if field in provided:
            val = getattr(body, field)
            setattr(source, field, val if val not in ("", None) else None)

    db.commit()
    return {"ok": True}


@router.post("/auto-assign-burners")
def auto_assign_burners(db: DB, _: CurrentUser):
    # Deprecated endpoint: burner assignment is disabled
    return {"ok": True, "reassigned": []}


@router.delete("/{source_id}")
def delete_ig_source(source_id: int, db: DB, _: CurrentUser):
    from app.models.ig_sources import IGSource

    source = db.query(IGSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    db.delete(source)
    db.commit()
    return {"ok": True}
