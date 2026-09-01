"""actually make the Discussion Card badge a full pill (labelRadius)

cb656e72c041 set the badge rect's rx/ry to height/2 (39), but a REAL render
test (2026-09-01, after the user asked for the same pill treatment on News
templates) showed the corners were still a modest rounded-rectangle, not a
pill — renderer/inject.js recomputes the badge's rx/ry at render time from
`badgeH * 0.28` by default, ignoring whatever rx/ry the template JSON sets,
UNLESS the badge object also carries a `labelRadius` property ("pill" or
-1 → radius = badgeH/2, a number → that exact px value). cb656e72c041's fix
was effectively a no-op for real output — it only ever affected the raw
JSON, never what actually got rendered. This migration adds
`labelRadius: "pill"` (default_templates.json already has it) to the same
4 already-seeded rows, verified this time against an actual render before
shipping.

Revision ID: 2000777df01a
Revises: cb656e72c041
Create Date: 2026-09-01
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "2000777df01a"
down_revision = "cb656e72c041"
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
    for name in _NAMES:
        op.get_bind().execute(
            text(
                """
                UPDATE design_templates
                   SET template_json = template_json #- '{objects,3,labelRadius}',
                       updated_at = now()
                 WHERE name = :name AND fanpage_id IS NULL
                """
            ),
            {"name": name},
        )
