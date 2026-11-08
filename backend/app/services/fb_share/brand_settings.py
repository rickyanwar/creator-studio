"""Brand sharing configuration stored separately from target fanpages."""

from sqlalchemy.orm import Session

from app.models.fb_group_share import FbShareBrandSettings
from app.schemas.fb_group_share import merge_settings


def get_brand_share(db: Session, fanpage_id: int) -> tuple[bool, dict]:
    row = db.get(FbShareBrandSettings, fanpage_id)
    return (row.enabled, merge_settings(row.settings)) if row else (False, merge_settings(None))


def set_brand_share(db: Session, fanpage_id: int, enabled: bool, settings: dict) -> None:
    row = db.get(FbShareBrandSettings, fanpage_id)
    if row is None:
        row = FbShareBrandSettings(fanpage_id=fanpage_id)
        db.add(row)
    row.enabled = enabled
    row.settings = settings
    db.flush()


def enabled_fanpage_ids(db: Session) -> list[int]:
    return [fanpage_id for (fanpage_id,) in db.query(FbShareBrandSettings.fanpage_id).filter(
        FbShareBrandSettings.enabled.is_(True),
    ).order_by(FbShareBrandSettings.fanpage_id).all()]
