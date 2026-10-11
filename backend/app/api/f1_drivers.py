import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from typing import List
from app.database import get_db
from app.api.deps import get_current_user
from app.models.f1_drivers import F1Driver
from app.schemas.f1_drivers import F1DriverCreate, F1DriverUpdate, F1DriverOut
from app.config import get_settings

router = APIRouter()

@router.get("", response_model=List[F1DriverOut])
def list_f1_drivers(
    season: int | None = None,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    if season is None:
        season = datetime.now().year
    return db.query(F1Driver).filter(F1Driver.season == season).order_by(F1Driver.surname).all()

@router.post("", response_model=F1DriverOut)
def create_f1_driver(
    driver_in: F1DriverCreate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    # Store uppercase
    driver_in.surname = driver_in.surname.upper()
    db_obj = F1Driver(**driver_in.model_dump())
    db.add(db_obj)
    try:
        db.commit()
        db.refresh(db_obj)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Driver with this season and surname already exists")
    return db_obj

@router.patch("/{driver_id}", response_model=F1DriverOut)
def update_f1_driver(
    driver_id: int,
    driver_in: F1DriverUpdate,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    db_obj = db.query(F1Driver).filter(F1Driver.id == driver_id).first()
    if not db_obj:
        raise HTTPException(status_code=404, detail="Driver not found")

    update_data = driver_in.model_dump(exclude_unset=True)
    if "surname" in update_data and update_data["surname"] is not None:
        update_data["surname"] = update_data["surname"].upper()

    for field, value in update_data.items():
        setattr(db_obj, field, value)

    try:
        db.commit()
        db.refresh(db_obj)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Driver with this season and surname already exists")
    return db_obj

@router.delete("/{driver_id}")
def delete_f1_driver(
    driver_id: int,
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    db_obj = db.query(F1Driver).filter(F1Driver.id == driver_id).first()
    if not db_obj:
        raise HTTPException(status_code=404, detail="Driver not found")
    db.delete(db_obj)
    db.commit()
    return {"ok": True}

@router.post("/{driver_id}/logo", response_model=F1DriverOut)
def upload_driver_logo(
    driver_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    _=Depends(get_current_user),
):
    db_obj = db.query(F1Driver).filter(F1Driver.id == driver_id).first()
    if not db_obj:
        raise HTTPException(status_code=404, detail="Driver not found")

    # Read up to 1 MB + 1 byte
    content = file.file.read(1024 * 1024 + 1)
    if len(content) > 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 1MB)")
    
    # Check magic bytes
    if content.startswith(b'\x89PNG\r\n\x1a\n'):
        ext = "png"
    elif content.startswith(b'\xff\xd8\xff'):
        ext = "jpg"
    elif content.startswith(b'RIFF') and content[8:12] == b'WEBP':
        ext = "webp"
    else:
        raise HTTPException(status_code=400, detail="Invalid file type, must be png/jpg/webp")

    filename = f"{db_obj.id}_{db_obj.season}.{ext}"
    s = get_settings()
    media_dir = os.path.realpath(os.path.join(s.storage_base_path, "team_logos"))
    os.makedirs(media_dir, exist_ok=True)
    file_path = os.path.realpath(os.path.join(media_dir, filename))
    
    if not file_path.startswith(media_dir):
        raise HTTPException(status_code=400, detail="Invalid path")

    with open(file_path, "wb") as f:
        f.write(content)

    rel_path = f"team_logos/{filename}"
    db_obj.team_logo_path = rel_path
    db.commit()
    db.refresh(db_obj)
    return db_obj
