"""Shared Facebook group catalog."""

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError

from app.api.deps import CurrentUser, DB
from app.models.fb_group_share import FbGroup
from app.schemas.fb_group_share import FbGroupIn, FbGroupOut, FbGroupPatch

router = APIRouter(prefix="/fb-groups", tags=["fb-groups"])


@router.get("", response_model=list[FbGroupOut])
def list_groups(db: DB, _: CurrentUser, niche: str | None = None, include_inactive: bool = False):
    query = db.query(FbGroup)
    if niche is not None:
        query = query.filter(FbGroup.niche == niche)
    if not include_inactive:
        query = query.filter(FbGroup.is_active.is_(True))
    return query.order_by(FbGroup.niche, FbGroup.name).all()


@router.get("/niches", response_model=list[str])
def list_niches(db: DB, _: CurrentUser):
    return [row[0] for row in db.query(FbGroup.niche).filter(FbGroup.is_active.is_(True)).distinct().order_by(FbGroup.niche).all()]


def _get_group(db, group_id):
    group = db.get(FbGroup, group_id)
    if group is None:
        raise HTTPException(404, "Group not found")
    return group


def _save_group(db, group):
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "Group URL already exists")
    db.refresh(group)
    return group


@router.post("", response_model=FbGroupOut)
def create_group(body: FbGroupIn, db: DB, _: CurrentUser):
    group = FbGroup(**body.model_dump())
    db.add(group)
    return _save_group(db, group)


@router.patch("/{group_id}", response_model=FbGroupOut)
def patch_group(group_id: int, body: FbGroupPatch, db: DB, _: CurrentUser):
    group = _get_group(db, group_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(group, key, value)
    return _save_group(db, group)


@router.delete("/{group_id}", response_model=FbGroupOut)
def delete_group(group_id: int, db: DB, _: CurrentUser):
    group = _get_group(db, group_id)
    group.is_active = False
    return _save_group(db, group)
