from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from pydantic import BaseModel

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/ig-sources", tags=["ig-sources"])


class AssignBurnerRequest(BaseModel):
    burner_id: Optional[int] = None


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
