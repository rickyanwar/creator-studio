"""Brand group-share settings and read-only sharing ledger."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import case, func

from app.api.deps import CurrentUser, DB
from app.models.fb_group_share import FbGroup, FbShareDelivery, FbGroupMembership, FbShareItem, FbShareTask
from app.models.social_publishing import PhoneDevice, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.schemas.fb_group_share import AccountOut, DeliveryIn, DeliveryOut, GroupShareIn, MembershipOut, MembershipsIn, merge_settings

router = APIRouter(prefix="/fanpages", tags=["group-share"])


def _brand(db, fanpage_id):
    brand = db.get(TargetFanpage, fanpage_id)
    if brand is None:
        raise HTTPException(404, "Brand not found")
    return brand


def _destinations(db, fanpage_id):
    return db.query(SocialDestination).filter(
        SocialDestination.content_project_id == fanpage_id,
        SocialDestination.kind.in_(("facebook_profile", "facebook_page")),
    )


def _destination(db, fanpage_id, destination_id):
    destination = _destinations(db, fanpage_id).filter(SocialDestination.id == destination_id).first()
    if destination is None:
        raise HTTPException(404, "Facebook account not found")
    return destination


def _account(db, destination, delivery):
    parent = db.get(SocialDestination, destination.parent_profile_id) if destination.parent_profile_id else None
    return {
        "destination_id": destination.id, "name": destination.name, "kind": destination.kind,
        "parent_profile_id": destination.parent_profile_id,
        "parent_profile_name": parent.name if parent else None,
        "delivery": DeliveryOut.model_validate(delivery) if delivery else None,
    }


@router.get("/{fanpage_id}/group-share")
def get_settings(fanpage_id: int, db: DB, _: CurrentUser):
    brand = _brand(db, fanpage_id)
    return {"enabled": brand.fb_share_enabled, "settings": merge_settings(brand.fb_share_settings)}


@router.put("/{fanpage_id}/group-share")
def put_settings(fanpage_id: int, body: GroupShareIn, db: DB, _: CurrentUser):
    brand = _brand(db, fanpage_id)
    brand.fb_share_enabled = body.enabled
    brand.fb_share_settings = body.settings.model_dump()
    db.commit()
    return {"enabled": brand.fb_share_enabled, "settings": merge_settings(brand.fb_share_settings)}


@router.get("/{fanpage_id}/group-share/accounts", response_model=list[AccountOut])
def list_accounts(fanpage_id: int, db: DB, _: CurrentUser):
    _brand(db, fanpage_id)
    rows = _destinations(db, fanpage_id).outerjoin(
        FbShareDelivery, FbShareDelivery.destination_id == SocialDestination.id,
    ).add_entity(FbShareDelivery).order_by(
        case((SocialDestination.kind == "facebook_profile", 0), else_=1), SocialDestination.name,
    ).all()
    return [_account(db, destination, delivery) for destination, delivery in rows]


@router.put("/{fanpage_id}/group-share/accounts/{destination_id}", response_model=AccountOut)
def put_delivery(fanpage_id: int, destination_id: int, body: DeliveryIn, db: DB, _: CurrentUser):
    _brand(db, fanpage_id)
    destination = _destination(db, fanpage_id, destination_id)
    delivery = db.query(FbShareDelivery).filter_by(destination_id=destination_id).first()
    if delivery is not None and (delivery.mode != body.mode or delivery.phone_device_id != body.phone_device_id):
        delivery.calibration = None
        delivery.calibrated_at = None
    if body.mode == "phone_auto" and body.is_active and (delivery is None or delivery.calibrated_at is None):
        raise HTTPException(422, "Calibrate first")
    if body.phone_device_id is not None and db.get(PhoneDevice, body.phone_device_id) is None:
        raise HTTPException(404, "Phone device not found")
    if delivery is None:
        delivery = FbShareDelivery(destination_id=destination_id)
        db.add(delivery)
    for key, value in body.model_dump().items():
        setattr(delivery, key, value)
    db.commit()
    db.refresh(delivery)
    return _account(db, destination, delivery)


def _memberships(db, fanpage_id):
    return db.query(FbGroupMembership).join(
        SocialDestination, SocialDestination.id == FbGroupMembership.destination_id,
    ).filter(SocialDestination.content_project_id == fanpage_id).order_by(
        FbGroupMembership.destination_id, FbGroupMembership.group_id,
    ).all()


@router.get("/{fanpage_id}/group-share/memberships", response_model=list[MembershipOut])
def list_memberships(fanpage_id: int, db: DB, _: CurrentUser):
    _brand(db, fanpage_id)
    return _memberships(db, fanpage_id)


@router.put("/{fanpage_id}/group-share/memberships", response_model=list[MembershipOut])
def put_memberships(fanpage_id: int, body: MembershipsIn, db: DB, _: CurrentUser):
    _brand(db, fanpage_id)
    for item in body.items:
        _destination(db, fanpage_id, item.destination_id)
        if db.get(FbGroup, item.group_id) is None:
            raise HTTPException(404, "Group not found")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for item in body.items:
        membership = db.query(FbGroupMembership).filter_by(destination_id=item.destination_id, group_id=item.group_id).first()
        if membership is None:
            membership = FbGroupMembership(destination_id=item.destination_id, group_id=item.group_id)
            db.add(membership)
        membership.status = item.status
        membership.checked_at = now
        if item.status == "joined" and membership.joined_at is None:
            membership.joined_at = now
        db.flush()
    db.commit()
    return _memberships(db, fanpage_id)


@router.get("/{fanpage_id}/group-share/tasks")
def list_tasks(fanpage_id: int, db: DB, _: CurrentUser, state: str | None = None, limit: int = Query(50, ge=1, le=200)):
    _brand(db, fanpage_id)
    query = db.query(FbShareTask, FbShareItem, FbGroup, SocialDestination).join(
        FbShareItem, FbShareItem.id == FbShareTask.item_id,
    ).join(FbGroup, FbGroup.id == FbShareTask.group_id).join(
        SocialDestination, SocialDestination.id == FbShareTask.destination_id,
    ).filter(FbShareItem.fanpage_id == fanpage_id, SocialDestination.content_project_id == fanpage_id)
    if state is not None:
        query = query.filter(FbShareTask.state == state)
    return [{
        "id": task.id, "item_id": task.item_id, "group_id": task.group_id,
        "group_name": group.name, "group_url": group.url,
        "destination_id": task.destination_id, "destination_name": destination.name,
        "delivery_mode": task.delivery_mode, "caption": item.caption,
        "scheduled_for": task.scheduled_for, "state": task.state,
        "post_url": task.post_url, "error": task.error, "media_kind": item.media_kind,
        "media_first": item.media[0] if item.media else None, "created_at": task.created_at,
    } for task, item, group, destination in query.order_by(FbShareTask.scheduled_for.desc(), FbShareTask.id.desc()).limit(limit).all()]


@router.get("/{fanpage_id}/group-share/summary")
def get_summary(fanpage_id: int, db: DB, _: CurrentUser):
    brand = _brand(db, fanpage_id)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    tasks = db.query(FbShareTask).join(FbShareItem, FbShareItem.id == FbShareTask.item_id).join(
        SocialDestination, SocialDestination.id == FbShareTask.destination_id,
    ).filter(FbShareItem.fanpage_id == fanpage_id, SocialDestination.content_project_id == fanpage_id)
    today = tasks.filter(FbShareTask.scheduled_for >= start, FbShareTask.scheduled_for < start + timedelta(days=1))
    paused = db.query(FbShareDelivery).join(SocialDestination, SocialDestination.id == FbShareDelivery.destination_id).filter(
        SocialDestination.content_project_id == fanpage_id, FbShareDelivery.paused_until > now,
    ).count()
    joined = db.query(func.count(func.distinct(FbGroupMembership.group_id))).join(
        SocialDestination, SocialDestination.id == FbGroupMembership.destination_id,
    ).filter(SocialDestination.content_project_id == fanpage_id, FbGroupMembership.status == "joined").scalar()
    return {
        "enabled": brand.fb_share_enabled,
        "today_published": today.filter(FbShareTask.state == "published").count(),
        "today_planned": today.filter(FbShareTask.state == "planned").count(),
        "pending_admin": tasks.filter(FbShareTask.state == "pending_admin").count(),
        "paused_accounts": paused, "groups_joined": joined,
    }
