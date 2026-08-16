"""Discussion Card: move badge+headline block lower (user feedback 2026-08-16)

titleAnchorBottom 1214 -> 1270 on all four discussion templates — the badge
and headline sat too close to the middle of the card; this pushes the whole
block (badge position is derived from the title's final top at render time,
so it follows automatically) further down toward the bottom margin.

Revision ID: a7b8c9d0e2f3
Revises: f6a7b8c9d0e2
Create Date: 2026-08-16
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e2f3"
down_revision = "f6a7b8c9d0e2"
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
                       placeholder_config = CAST(:pconf AS jsonb),
                       updated_at = now()
                 WHERE name = :name AND fanpage_id IS NULL
                """
            ),
            {
                "tjson": json.dumps(tpl.get("template_json")),
                "pconf": json.dumps(tpl.get("placeholder_config")),
                "name": name,
            },
        )


def downgrade():
    pass
