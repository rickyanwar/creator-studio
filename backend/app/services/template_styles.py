"""Pure helpers and data structures for brand styles and design templates."""

import copy
import re
from typing import Optional

STYLES = ("gp", "highlight", "classic")
CATEGORIES = ("news", "quote", "discussion")
STYLE_LABELS = {
    "gp": "GP Racing",
    "highlight": "Highlight",
    "classic": "Classic",
}

STYLE_TEMPLATE_NAMES = {
    "gp": {
        "news": "GP News — Breaking",
        "quote": "GP Quote",
        "discussion": "GP Discussion",
    },
    "highlight": {
        "news": "News Highlight — Green · Center (Quote overlay)",
        "quote": "Quote Card (word-highlight)",
        "discussion": "Highlight Discussion",
    },
    "classic": {
        "news": "News Highlight — Red · Center",
        "quote": "Quote Card (name badge)",
        "discussion": "Classic Discussion",
    },
}

STYLE_DEFAULTS = {
    "gp": {"primary": "#E8362B", "secondary": "#E8362B"},
    "highlight": {"primary": "#C7F754", "secondary": "#C7F754", "quoteMarks": "#FEFEFC"},
    "classic": {"primary": "#EC3324", "secondary": "#EC3324"},
}

LEGACY_TEMPLATE_STYLE = {
    "MotoGP News (two-tone headline)": "gp",
    "Quote Card (word-highlight, name badge)": "classic",
    "News Highlight — Red · Left": "classic",
    "News Highlight — Yellow · Left": "classic",
    "News Highlight — Green · Left": "highlight",
    "News Highlight — Yellow · Center": "classic",
    "News Highlight — Green · Center": "highlight",
    "Discussion Card — Red · Center": "classic",
    "Discussion Card — Yellow · Center": "classic",
    "Discussion Card — Green · Center": "highlight",
    "Discussion Card — Green · Center (Quote overlay)": "highlight",
    "Quote Card — Green · Center": "highlight",
    "Designer — Quote": "gp",
    "Designer — News": "gp",
    "Designer — Update": "gp",
}

QUEUED_JOB_STATUSES = ("pending_caption", "pending_design", "rendering")
HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")

_STYLE_OF_NAME = dict(LEGACY_TEMPLATE_STYLE)
for _style, _cats in STYLE_TEMPLATE_NAMES.items():
    for _name in _cats.values():
        _STYLE_OF_NAME[_name] = _style


def style_of_template_name(name: str) -> Optional[str]:
    """Return style of a template name (styled or legacy), or None."""
    return _STYLE_OF_NAME.get(name)


def upgrade_kept_template(tj: dict, style: str, category: str) -> dict:
    """Return upgraded template_json with style defaults and badge adjustments."""
    if style not in STYLES:
        raise ValueError(f"Unknown style: {style}")
    if category not in CATEGORIES:
        raise ValueError(f"Unknown category: {category}")

    out = copy.deepcopy(tj)
    out["accentFollowsFanpage"] = True

    brand_defaults = out.get("brandDefaults")
    if not isinstance(brand_defaults, dict):
        out["brandDefaults"] = dict(STYLE_DEFAULTS[style])

    if style in ("highlight", "classic") and category == "news":
        for obj in out.get("objects", []):
            role = obj.get("placeholderRole")
            if role == "label":
                obj["fontSize"] = 44
                obj["labelPadX"] = 42
                obj["labelPadY"] = 12
                obj["text"] = "BREAKING"
                obj["height"] = 50
            elif role == "labelBadge":
                obj["labelFixedHeight"] = 73
                obj["height"] = 73
                obj["width"] = 334
                obj["left"] = 373
                obj["rx"] = 36.5
                obj["ry"] = 36.5
                obj["labelRadius"] = "pill"

    return out


def make_discussion_template(news_tj: dict, style: str) -> dict:
    """Generate discussion template from upgraded news template."""
    out = upgrade_kept_template(news_tj, style, "news")

    filtered_objects = []
    for obj in out.get("objects", []):
        role = obj.get("placeholderRole")
        if role in ("subtitle", "subtitleBadge"):
            continue
        if role == "label":
            obj["text"] = "DISCUSSION"
        elif role == "title" and style in ("highlight", "classic"):
            obj["titleAnchorBottom"] = 1270
        filtered_objects.append(obj)

    out["objects"] = filtered_objects
    return out


def legacy_accent_color(tj: dict) -> Optional[str]:
    """Extract legacy accent hex color from labelBadge fill or titleAccentColor."""
    objects = tj.get("objects", []) if isinstance(tj, dict) else []

    for obj in objects:
        if obj.get("placeholderRole") == "labelBadge":
            fill = obj.get("fill")
            if isinstance(fill, str) and HEX_RE.match(fill):
                return fill.upper()

    for obj in objects:
        if obj.get("placeholderRole") == "title":
            accent = obj.get("titleAccentColor")
            if isinstance(accent, str) and HEX_RE.match(accent):
                return accent.upper()

    return None


def legacy_quote_mark_color(tj: dict) -> Optional[str]:
    """Extract quote mark hex color from textbox quoteIcon object."""
    objects = tj.get("objects", []) if isinstance(tj, dict) else []

    for obj in objects:
        if obj.get("placeholderRole") == "quoteIcon":
            obj_type = str(obj.get("type", "")).lower()
            if obj_type == "textbox":
                fill = obj.get("fill")
                if isinstance(fill, str) and HEX_RE.match(fill):
                    return fill.upper()

    return None


def plan_brand_move(fanpages: list[dict], templates: list[dict], jobs: list[dict]) -> dict:
    """Plan migration of fan pages, template defaults, colors, and queued jobs to styles."""
    style_ids: dict[str, dict[str, int]] = {s: {} for s in STYLES}
    for t in templates:
        if t.get("fanpage_id") is None:
            t_name = t.get("name")
            for s in STYLES:
                for cat in CATEGORIES:
                    if t_name == STYLE_TEMPLATE_NAMES[s][cat]:
                        style_ids[s][cat] = t["id"]

    for s in STYLES:
        for cat in CATEGORIES:
            expected_name = STYLE_TEMPLATE_NAMES[s][cat]
            if cat not in style_ids[s]:
                raise ValueError(f"missing style template: {expected_name}")

    by_id = {t["id"]: t for t in templates}
    all_styled_names = {STYLE_TEMPLATE_NAMES[s][c] for s in STYLES for c in CATEGORIES}

    def _is_styled(t: dict) -> bool:
        return t.get("fanpage_id") is None and t.get("name") in all_styled_names

    def _is_legacy(t: dict) -> bool:
        return t.get("fanpage_id") is None and t.get("name") in LEGACY_TEMPLATE_STYLE

    fanpage_outputs = []
    fanpage_styles: dict[int, str] = {}

    for fp in fanpages:
        fp_id = fp["id"]
        chosen_style: Optional[str] = None
        for key in ("default_news_template_id", "default_quote_template_id", "default_discussion_template_id"):
            tid = fp.get(key)
            if tid is not None and tid in by_id:
                t = by_id[tid]
                if _is_styled(t) or _is_legacy(t):
                    s = style_of_template_name(t.get("name", ""))
                    if s:
                        chosen_style = s
                        break

        if not chosen_style:
            chosen_style = "gp"
        fanpage_styles[fp_id] = chosen_style

        new_news_id = style_ids[chosen_style]["news"]
        cur_news = fp.get("default_news_template_id")
        if cur_news is not None and cur_news in by_id:
            t = by_id[cur_news]
            if not (_is_styled(t) or _is_legacy(t)):
                new_news_id = cur_news

        new_quote_id = style_ids[chosen_style]["quote"]
        cur_quote = fp.get("default_quote_template_id")
        if cur_quote is not None and cur_quote in by_id:
            t = by_id[cur_quote]
            if not (_is_styled(t) or _is_legacy(t)):
                new_quote_id = cur_quote

        new_disc_id = style_ids[chosen_style]["discussion"]
        cur_disc = fp.get("default_discussion_template_id")
        if cur_disc is not None and cur_disc in by_id:
            t = by_id[cur_disc]
            if not (_is_styled(t) or _is_legacy(t)):
                new_disc_id = cur_disc

        raw_accent = fp.get("accent_color")
        if isinstance(raw_accent, str) and HEX_RE.match(raw_accent):
            brand_override = True
            accent_color = raw_accent
            accent_secondary_color = None
            quote_mark_color = None
        else:
            legacy_tpl = None
            for tid in (cur_news, cur_quote):
                if tid is not None and tid in by_id:
                    t = by_id[tid]
                    if _is_legacy(t):
                        legacy_tpl = t
                        break

            old = legacy_accent_color(legacy_tpl.get("template_json") or {}) if legacy_tpl else None
            primary_default = STYLE_DEFAULTS[chosen_style]["primary"]
            if old and old.upper() != primary_default.upper():
                brand_override = True
                accent_color = old
                accent_secondary_color = old
                quote_tpl = by_id.get(cur_quote) if cur_quote is not None else None
                if quote_tpl and _is_legacy(quote_tpl):
                    quote_mark_color = legacy_quote_mark_color(quote_tpl.get("template_json") or {})
                else:
                    quote_mark_color = STYLE_DEFAULTS[chosen_style].get("quoteMarks")
            else:
                brand_override = False
                accent_color = None
                accent_secondary_color = None
                quote_mark_color = None

        fanpage_outputs.append({
            "id": fp_id,
            "template_style": chosen_style,
            "default_news_template_id": new_news_id,
            "default_quote_template_id": new_quote_id,
            "default_discussion_template_id": new_disc_id,
            "brand_override": brand_override,
            "accent_color": accent_color,
            "accent_secondary_color": accent_secondary_color,
            "quote_mark_color": quote_mark_color,
        })

    job_outputs = []
    for job in jobs:
        dt_id = job.get("design_template_id")
        if dt_id is None or dt_id not in by_id:
            continue
        tpl = by_id[dt_id]
        if not _is_legacy(tpl):
            continue

        cat = tpl.get("category")
        if cat not in CATEGORIES:
            cat = "news"

        job_fp_id = job.get("fanpage_id")
        if job_fp_id is not None and job_fp_id in fanpage_styles:
            style = fanpage_styles[job_fp_id]
        else:
            style = LEGACY_TEMPLATE_STYLE[tpl["name"]]

        new_dt_id = style_ids[style][cat]
        if new_dt_id != dt_id:
            job_outputs.append({
                "id": job["id"],
                "design_template_id": new_dt_id,
            })

    return {
        "fanpages": fanpage_outputs,
        "jobs": job_outputs,
    }


def style_template_ids(db, style: str) -> dict[str, Optional[int]]:
    """Return map of category -> shared template id for a style."""
    from app.models.design_templates import DesignTemplate

    rows = (
        db.query(DesignTemplate.id, DesignTemplate.category)
        .filter(
            DesignTemplate.fanpage_id.is_(None),
            DesignTemplate.style == style,
            DesignTemplate.category.in_(CATEGORIES),
            DesignTemplate.is_hidden == False,
        )
        .order_by(DesignTemplate.id.asc())
        .all()
    )
    out: dict[str, Optional[int]] = {cat: None for cat in CATEGORIES}
    for row_id, cat in rows:
        if cat in out and out[cat] is None:
            out[cat] = row_id
    return out


def style_defaults(db, style: str) -> dict:
    """Return brandDefaults dict of that style's news template (fallback STYLE_DEFAULTS)."""
    from app.models.design_templates import DesignTemplate

    fallback = dict(STYLE_DEFAULTS[style]) if style in STYLE_DEFAULTS else {}

    row = (
        db.query(DesignTemplate)
        .filter(
            DesignTemplate.fanpage_id.is_(None),
            DesignTemplate.style == style,
            DesignTemplate.category == "news",
            DesignTemplate.is_hidden == False,
        )
        .order_by(DesignTemplate.id.asc())
        .first()
    )
    if row and isinstance(row.template_json, dict):
        bd = row.template_json.get("brandDefaults")
        if isinstance(bd, dict):
            return dict(bd)
    return fallback

