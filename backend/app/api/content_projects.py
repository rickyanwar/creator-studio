"""Content Projects API router."""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import func, null

from app.api.deps import CurrentUser, DB
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.schemas.social_media import ContentProjectCreate, ContentProjectOut

router = APIRouter(prefix="/content-projects", tags=["content-projects"])


@router.get("", response_model=list[ContentProjectOut])
def list_content_projects(db: DB, _: CurrentUser):
    """List existing TargetFanpage rows as reusable content projects with destination count."""
    results = (
        db.query(TargetFanpage, func.count(SocialDestination.id).label("dest_count"))
        .outerjoin(SocialDestination, SocialDestination.content_project_id == TargetFanpage.id)
        .group_by(TargetFanpage.id)
        .order_by(TargetFanpage.name)
        .all()
    )
    return [
        ContentProjectOut(
            id=proj.id,
            name=proj.name,
            repliz_account_id=proj.repliz_account_id,
            is_connected=proj.is_connected,
            is_active=proj.is_active,
            timezone=proj.timezone,
            target_country=proj.target_country,
            caption_language=proj.caption_language,
            caption_tone=proj.caption_tone,
            destination_count=count,
            created_at=proj.created_at,
            updated_at=proj.updated_at,
        )
        for proj, count in results
    ]


@router.post("", response_model=ContentProjectOut, status_code=status.HTTP_201_CREATED)
def create_content_project(payload: ContentProjectCreate, db: DB, _: CurrentUser):
    """Create a locally managed content project without requiring Repliz credentials."""
    clean_name = payload.name.strip()
    if not clean_name:
        raise HTTPException(status_code=400, detail="Project name cannot be blank")

    existing = (
        db.query(TargetFanpage)
        .filter(func.lower(TargetFanpage.name) == func.lower(clean_name))
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=400,
            detail=f"Content project '{clean_name}' already exists",
        )

    target_country = "GB"
    if "target_country" in payload.model_fields_set:
        target_country = (
            payload.target_country if payload.target_country is not None else null()
        )

    project_kwargs = {
        "name": clean_name,
        "repliz_account_id": None,
        "is_connected": True,
        "is_active": False,
        "timezone": payload.timezone,
        "target_country": target_country,
    }
    if payload.caption_language is not None:
        project_kwargs["caption_language"] = payload.caption_language
        project_kwargs["mode2_caption_language"] = payload.caption_language
    if payload.caption_tone is not None:
        project_kwargs["caption_tone"] = payload.caption_tone
        project_kwargs["mode2_caption_tone"] = payload.caption_tone

    project = TargetFanpage(**project_kwargs)
    db.add(project)
    db.commit()
    db.refresh(project)

    return ContentProjectOut(
        id=project.id,
        name=project.name,
        repliz_account_id=project.repliz_account_id,
        is_connected=project.is_connected,
        is_active=project.is_active,
        timezone=project.timezone,
        target_country=project.target_country,
        caption_language=project.caption_language,
        caption_tone=project.caption_tone,
        destination_count=0,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )


@router.get("/{project_id}", response_model=ContentProjectOut)
def get_content_project(project_id: int, db: DB, _: CurrentUser):
    """Get a content project summary by ID with destination count."""
    project = db.query(TargetFanpage).filter_by(id=project_id).first()
    if not project:
        raise HTTPException(status_code=404, detail="Content project not found")

    dest_count = (
        db.query(func.count(SocialDestination.id))
        .filter(SocialDestination.content_project_id == project.id)
        .scalar()
        or 0
    )

    return ContentProjectOut(
        id=project.id,
        name=project.name,
        repliz_account_id=project.repliz_account_id,
        is_connected=project.is_connected,
        is_active=project.is_active,
        timezone=project.timezone,
        target_country=project.target_country,
        caption_language=project.caption_language,
        caption_tone=project.caption_tone,
        destination_count=dest_count,
        created_at=project.created_at,
        updated_at=project.updated_at,
    )
