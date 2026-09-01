"""round Discussion Card labelBadge corners to a full pill shape

User reference (2026-09-01): wants the Discussion/Hot Take badge's corners
fully rounded into a pill/stadium shape (like a typical news-site tag
badge), not the current modest rounded-rectangle look. The badge rect is
360x78 with rx/ry=20 (~26% of height) — a full pill needs rx/ry=height/2=39.

Same "replace template_json from the current seed file by name" pattern as
a1b2c3d4e5f7 (rename+recolor) rather than a surgical JSON-path SQL patch —
default_templates.json already has the fix (rx/ry 20->39) for all 4
Discussion Card variants (Red/Yellow/Green/Green-with-quote-overlay); this
migration just pushes that same JSON onto the already-seeded rows
(fanpage_id IS NULL only — never touches a fanpage's own customized copy).

Revision ID: cb656e72c041
Revises: 8cf01a8c836c
Create Date: 2026-09-01
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "cb656e72c041"
down_revision = "8cf01a8c836c"
branch_labels = None
depends_on = None

_NAMES = [
    "Discussion Card — Red · Center",
    "Discussion Card — Yellow · Center",
    "Discussion Card — Green · Center",
    "Discussion Card — Green · Center (Quote overlay)",
]


def upgrade():
    from app.seeds import load_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}

    for name in _NAMES:
        tpl = templates_by_name.get(name)
        if not tpl:
            print(f"[warn] {name!r} not found in default_templates.json — skipping")
            continue
        op.get_bind().execute(
            text(
                """
                UPDATE design_templates
                   SET template_json = CAST(:tjson AS jsonb),
                       updated_at = now()
                 WHERE name = :name AND fanpage_id IS NULL
                """
            ),
            {"tjson": json.dumps(tpl.get("template_json")), "name": name},
        )


def downgrade():
    from app.seeds import load_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}

    for name in _NAMES:
        tpl = templates_by_name.get(name)
        if not tpl:
            continue
        tj = tpl.get("template_json")
        for obj in tj.get("objects", []):
            if obj.get("placeholderRole") == "labelBadge":
                obj["rx"] = 20
                obj["ry"] = 20
        op.get_bind().execute(
            text(
                """
                UPDATE design_templates
                   SET template_json = CAST(:tjson AS jsonb),
                       updated_at = now()
                 WHERE name = :name AND fanpage_id IS NULL
                """
            ),
            {"tjson": json.dumps(tj), "name": name},
        )
