import copy
from datetime import timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DB
from app.services.template_styles import (
    CATEGORIES,
    LEGACY_TEMPLATE_STYLE,
    STYLE_LABELS,
    STYLES,
    style_defaults,
)

router = APIRouter(prefix="/templates", tags=["design-templates"])


class TemplateBody(BaseModel):
    name: str
    fanpage_id: Optional[int] = None  # null = shared template
    canvas_width: Optional[int] = 1080
    canvas_height: Optional[int] = 1080
    template_json: Optional[dict[str, Any]] = None
    placeholder_config: Optional[dict[str, Any]] = None
    is_default: Optional[bool] = False
    category: Optional[str] = None  # "quote" | "news" | null (uncategorized)


class UpdateTemplateBody(TemplateBody):
    # partial update: every field must default to None, otherwise the
    # TemplateBody defaults (1080×1080, is_default=False) silently overwrite
    # stored values whenever the editor saves without sending them
    name: Optional[str] = None
    canvas_width: Optional[int] = None
    canvas_height: Optional[int] = None
    is_default: Optional[bool] = None


class StyleDefaultsBody(BaseModel):
    primary: str = Field(..., pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    badge: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    badgeText: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    quoteMarks: Optional[str] = Field(None, pattern=r"^#[0-9A-Fa-f]{6}$")
    streak: Optional[bool] = None


def _serialize(t, include_json: bool = False):
    out = {
        "id": t.id,
        "fanpage_id": t.fanpage_id,
        "name": t.name,
        "canvas_width": t.canvas_width,
        "canvas_height": t.canvas_height,
        "is_default": t.is_default,
        "category": t.category,
        "style": t.style,
        "is_hidden": bool(t.is_hidden),
        "brand_defaults": (t.template_json or {}).get("brandDefaults") if isinstance(t.template_json, dict) else None,
        "replacement_style": LEGACY_TEMPLATE_STYLE.get(t.name) if t.fanpage_id is None else None,
        "has_content": t.template_json is not None,
        "placeholder_config": t.placeholder_config,
        "updated_at": t.updated_at.replace(tzinfo=timezone.utc).isoformat() if t.updated_at else None,
    }
    if include_json:
        out["template_json"] = t.template_json
    return out


def _serialize_style(db, style: str) -> dict[str, Any]:
    from app.models.design_templates import DesignTemplate

    raw_defaults = style_defaults(db, style)
    allowed_keys = ("primary", "secondary", "badge", "badgeText", "quoteMarks", "streak")
    defaults = {k: raw_defaults[k] for k in allowed_keys if k in raw_defaults and raw_defaults[k] is not None}

    tpl_rows = (
        db.query(DesignTemplate.id, DesignTemplate.name, DesignTemplate.category)
        .filter(
            DesignTemplate.fanpage_id.is_(None),
            DesignTemplate.style == style,
            DesignTemplate.category.in_(CATEGORIES),
            DesignTemplate.is_hidden == False,
        )
        .order_by(DesignTemplate.id.asc())
        .all()
    )
    templates: dict[str, Optional[dict[str, Any]]] = {cat: None for cat in CATEGORIES}
    for row_id, row_name, cat in tpl_rows:
        if templates[cat] is None:
            templates[cat] = {"id": row_id, "name": row_name}

    return {
        "style": style,
        "label": STYLE_LABELS.get(style, style),
        "defaults": defaults,
        "templates": templates,
    }


@router.get("")
def list_templates(db: DB, _: CurrentUser, fanpage_id: Optional[int] = Query(None)):
    from app.models.design_templates import DesignTemplate

    q = db.query(DesignTemplate)
    if fanpage_id is not None:
        # fanpage-specific plus shared templates
        q = q.filter((DesignTemplate.fanpage_id == fanpage_id) | (DesignTemplate.fanpage_id.is_(None)))
    # include_json so the gallery can render a live Fabric.js thumbnail per card
    return [_serialize(t, include_json=True) for t in q.order_by(DesignTemplate.name).all()]


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
        category=body.category,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize(t, include_json=True)


@router.get("/styles")
def list_template_styles(db: DB, _: CurrentUser):
    return [_serialize_style(db, s) for s in STYLES]


@router.put("/styles/{style}/defaults")
def update_style_defaults(style: str, body: StyleDefaultsBody, db: DB, _: CurrentUser):
    from sqlalchemy.orm.attributes import flag_modified
    from app.models.design_templates import DesignTemplate

    if style not in STYLES:
        raise HTTPException(status_code=404, detail="Unknown style")

    new_defaults = {k: v for k, v in body.model_dump().items() if v is not None}

    tpls = (
        db.query(DesignTemplate)
        .filter(
            DesignTemplate.fanpage_id.is_(None),
            DesignTemplate.style == style,
            DesignTemplate.is_hidden == False,
        )
        .all()
    )
    for t in tpls:
        tj = copy.deepcopy(t.template_json or {})
        tj["brandDefaults"] = new_defaults
        t.template_json = tj
        flag_modified(t, "template_json")

    db.commit()
    return _serialize_style(db, style)


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
    if body.category is not None:
        t.category = body.category
    if body.is_default is not None:
        t.is_default = body.is_default
        if body.is_default:
            cat_filter = (
                DesignTemplate.category.is_(None)
                if t.category is None
                else (DesignTemplate.category == t.category)
            )
            fp_filter = (
                DesignTemplate.fanpage_id.is_(None)
                if t.fanpage_id is None
                else (DesignTemplate.fanpage_id == t.fanpage_id)
            )
            db.query(DesignTemplate).filter(
                DesignTemplate.id != t.id,
                fp_filter,
                cat_filter,
            ).update({DesignTemplate.is_default: False}, synchronize_session=False)

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
