from datetime import timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.api.deps import CurrentUser, DB

router = APIRouter(prefix="/templates", tags=["design-templates"])


class TemplateBody(BaseModel):
    name: str
    fanpage_id: Optional[int] = None  # null = shared template
    canvas_width: Optional[int] = 1080
    canvas_height: Optional[int] = 1080
    template_json: Optional[dict[str, Any]] = None
    placeholder_config: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = False


class UpdateTemplateBody(TemplateBody):
    # partial update: every field must default to None, otherwise the
    # TemplateBody defaults (1080×1080, is_default=False) silently overwrite
    # stored values whenever the editor saves without sending them
    name: Optional[str] = None
    canvas_width: Optional[int] = None
    canvas_height: Optional[int] = None
    is_default: Optional[bool] = None


def _serialize(t, include_json: bool = False):
    out = {
        "id": t.id,
        "fanpage_id": t.fanpage_id,
        "name": t.name,
        "canvas_width": t.canvas_width,
        "canvas_height": t.canvas_height,
        "is_default": t.is_default,
        "has_content": t.template_json is not None,
        "placeholder_config": t.placeholder_config,
        "updated_at": t.updated_at.replace(tzinfo=timezone.utc).isoformat() if t.updated_at else None,
    }
    if include_json:
        out["template_json"] = t.template_json
    return out


@router.get("")
def list_templates(db: DB, _: CurrentUser, fanpage_id: Optional[int] = Query(None)):
    from app.models.design_templates import DesignTemplate

    q = db.query(DesignTemplate)
    if fanpage_id is not None:
        # fanpage-specific plus shared templates
        q = q.filter((DesignTemplate.fanpage_id == fanpage_id) | (DesignTemplate.fanpage_id.is_(None)))
    return [_serialize(t) for t in q.order_by(DesignTemplate.name).all()]


@router.post("")
def create_template(body: TemplateBody, db: DB, _: CurrentUser):
    from app.models.design_templates import DesignTemplate

    t = DesignTemplate(
        name=body.name.strip(),
        fanpage_id=body.fanpage_id,
        canvas_width=body.canvas_width or 1080,
        canvas_height=body.canvas_height or 1080,
        template_json=body.template_json,
        placeholder_config=body.placeholder_config,
        is_default=bool(body.is_default),
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize(t, include_json=True)


@router.get("/{template_id}")
def get_template(template_id: int, db: DB, _: CurrentUser):
    from app.models.design_templates import DesignTemplate

    t = db.query(DesignTemplate).filter_by(id=template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    return _serialize(t, include_json=True)


@router.put("/{template_id}")
def update_template(template_id: int, body: UpdateTemplateBody, db: DB, _: CurrentUser):
    from app.models.design_templates import DesignTemplate

    t = db.query(DesignTemplate).filter_by(id=template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    if body.name is not None:
        t.name = body.name.strip()
    if body.fanpage_id is not None:
        t.fanpage_id = body.fanpage_id
    if body.canvas_width is not None:
        t.canvas_width = body.canvas_width
    if body.canvas_height is not None:
        t.canvas_height = body.canvas_height
    if body.template_json is not None:
        t.template_json = body.template_json
    if body.placeholder_config is not None:
        t.placeholder_config = body.placeholder_config
    if body.is_default is not None:
        t.is_default = body.is_default
        if body.is_default:
            # only one default per fanpage scope
            db.query(DesignTemplate).filter(
                DesignTemplate.id != t.id,
                DesignTemplate.fanpage_id == t.fanpage_id,
            ).update({DesignTemplate.is_default: False})

    db.commit()
    db.refresh(t)
    return _serialize(t, include_json=True)


@router.delete("/{template_id}")
def delete_template(template_id: int, db: DB, _: CurrentUser):
    from app.models.design_templates import DesignTemplate

    t = db.query(DesignTemplate).filter_by(id=template_id).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")

    db.delete(t)
    db.commit()
    return {"ok": True}
