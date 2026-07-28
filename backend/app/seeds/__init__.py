"""Seed data shipped with the app (travels with the repo).

`default_templates.json` holds shared design templates that should exist on any
fresh install / server migration. `seed_default_templates()` inserts the ones
that aren't already present — it is idempotent, so it is safe to run repeatedly
(from a migration and/or on startup).

`default_gallery_keywords.json` holds a starter set of gallery search keywords
(current MotoGP/F1 grids, UFC/boxing champions, NBA stars) tagged with a niche,
seeded the same idempotent way via `seed_default_gallery_keywords()`.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

_SEED_FILE = os.path.join(os.path.dirname(__file__), "default_templates.json")
_GALLERY_KEYWORDS_SEED_FILE = os.path.join(os.path.dirname(__file__), "default_gallery_keywords.json")


def load_default_templates() -> list[dict]:
    try:
        with open(_SEED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not read default_templates.json: %s", exc)
        return []


def seed_default_templates(conn) -> int:
    """Insert missing shared (fanpage_id IS NULL) default templates via a raw
    connection (usable from an Alembic migration). Returns the count inserted."""
    from sqlalchemy import text

    inserted = 0
    for tpl in load_default_templates():
        name = tpl.get("name")
        if not name:
            continue
        exists = conn.execute(
            text("SELECT 1 FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
            {"n": name},
        ).first()
        if exists:
            continue
        conn.execute(
            text(
                """
                INSERT INTO design_templates
                    (fanpage_id, name, template_json, placeholder_config,
                     canvas_width, canvas_height, is_default, category, created_at, updated_at)
                VALUES
                    (NULL, :name, CAST(:tjson AS jsonb), CAST(:pconf AS jsonb),
                     :cw, :ch, :is_default, :category, now(), now())
                """
            ),
            {
                "name": name,
                "tjson": json.dumps(tpl.get("template_json")),
                "pconf": json.dumps(tpl.get("placeholder_config")),
                "cw": tpl.get("canvas_width", 1080),
                "ch": tpl.get("canvas_height", 1080),
                "is_default": bool(tpl.get("is_default", False)),
                "category": tpl.get("category"),
            },
        )
        inserted += 1
    return inserted


def load_default_gallery_keywords() -> list[dict]:
    try:
        with open(_GALLERY_KEYWORDS_SEED_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not read default_gallery_keywords.json: %s", exc)
        return []


def seed_default_gallery_keywords(conn) -> int:
    """Insert missing gallery keywords via a raw connection (usable from an
    Alembic migration). Returns the count inserted."""
    from sqlalchemy import text

    inserted = 0
    for kw in load_default_gallery_keywords():
        keyword = (kw.get("keyword") or "").strip().lower()
        if not keyword:
            continue
        exists = conn.execute(
            text("SELECT 1 FROM gallery_keywords WHERE keyword = :k"),
            {"k": keyword},
        ).first()
        if exists:
            continue
        conn.execute(
            text(
                """
                INSERT INTO gallery_keywords
                    (keyword, niche, is_active, max_images, max_pages,
                     min_width, min_height, source_engine, license_filter, created_at)
                VALUES
                    (:keyword, :niche, true, :max_images, :max_pages,
                     :min_width, :min_height, :source_engine, :license_filter, now())
                """
            ),
            {
                "keyword": keyword,
                "niche": kw.get("niche"),
                "max_images": kw.get("max_images", 50),
                "max_pages": kw.get("max_pages", 10),
                "min_width": kw.get("min_width", 200),
                "min_height": kw.get("min_height", 200),
                "source_engine": kw.get("source_engine", "9router"),
                "license_filter": kw.get("license_filter", "commercial,modify"),
            },
        )
        inserted += 1
    return inserted
