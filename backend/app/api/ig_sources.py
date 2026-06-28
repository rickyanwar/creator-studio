from fastapi import APIRouter, Query, HTTPException
from typing import Optional, List
from pydantic import BaseModel, field_validator

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/ig-sources", tags=["ig-sources"])


class AssignBurnerRequest(BaseModel):
    burner_id: Optional[int] = None


class UpdateIGSourceRequest(BaseModel):
    ig_username: Optional[str] = None
    is_active: Optional[bool] = None
    album_image_indices: Optional[List[int]] = None
    scraper_backend: Optional[str] = None  # "auto" | "instagrapi" | "flashapi"

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
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource
    from app.models.burner_accounts import BurnerAccount

    q = db.query(IGSource)
    sources = q.order_by(IGSource.ig_username).all()

    result = []
    for s in sources:
        active_links = db.query(FanpageSource).filter_by(ig_source_id=s.id, is_active=True).count()
        if orphan_only and active_links > 0:
            continue

        burner = db.query(BurnerAccount).filter_by(id=s.burner_account_id).first() if s.burner_account_id else None

        result.append({
            "id": s.id,
            "ig_username": s.ig_username,
            "ig_user_id": s.ig_user_id,
            "burner_id": s.burner_account_id,
            "burner_username": burner.ig_username if burner else None,
            "burner_status": burner.status.value if burner else None,
            "is_active": s.is_active,
            "last_checked_at": s.last_checked_at,
            "last_seen_post_id": s.last_seen_post_id,
            "active_fanpage_count": active_links,
            "album_image_indices": s.album_image_indices or [1],
            "scraper_backend": (s.scraper_backend.value if s.scraper_backend else "auto"),
            "last_crawl_error": s.last_crawl_error,
        })

    return result


@router.patch("/{source_id}/assign-burner")
def assign_burner(source_id: int, body: AssignBurnerRequest, db: DB, _: CurrentUser):
    from app.models.ig_sources import IGSource
    from app.models.burner_accounts import BurnerAccount

    source = db.query(IGSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    if body.burner_id is not None:
        burner = db.query(BurnerAccount).filter_by(id=body.burner_id).first()
        if not burner:
            raise HTTPException(status_code=404, detail="Burner not found")

    source.burner_account_id = body.burner_id
    db.commit()
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
        try:
            source.scraper_backend = ScraperBackend(body.scraper_backend)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid scraper_backend: must be auto, instagrapi, or flashapi")

    db.commit()
    return {"ok": True}


@router.post("/auto-assign-burners")
def auto_assign_burners(db: DB, _: CurrentUser):
    """Reassign all sources whose burner is challenged/missing to a random active burner."""
    from app.models.ig_sources import IGSource
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    import random

    active_burners = (
        db.query(BurnerAccount)
        .filter(BurnerAccount.status == BurnerStatus.active)
        .all()
    )
    if not active_burners:
        raise HTTPException(status_code=400, detail="No active burners available")

    sources = db.query(IGSource).all()
    reassigned = []

    for source in sources:
        needs_reassign = True
        if source.burner_account_id:
            assigned = db.query(BurnerAccount).filter_by(id=source.burner_account_id).first()
            if assigned and assigned.status == BurnerStatus.active:
                needs_reassign = False

        if needs_reassign:
            new_burner = random.choice(active_burners)
            source.burner_account_id = new_burner.id
            reassigned.append({"source": source.ig_username, "burner": new_burner.ig_username})

    db.commit()
    return {"ok": True, "reassigned": reassigned}


@router.delete("/{source_id}")
def delete_ig_source(source_id: int, db: DB, _: CurrentUser):
    from app.models.ig_sources import IGSource

    source = db.query(IGSource).filter_by(id=source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    db.delete(source)
    db.commit()
    return {"ok": True}
