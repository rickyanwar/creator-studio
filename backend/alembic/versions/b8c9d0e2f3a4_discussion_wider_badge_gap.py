"""Discussion Card: more space between badge and headline (user feedback 2026-08-16)

labelGap 26 (code default) -> 55 (explicit) on all four discussion templates
— the badge sat too close to the headline text below it.

Revision ID: b8c9d0e2f3a4
Revises: a7b8c9d0e2f3
Create Date: 2026-08-16
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "b8c9d0e2f3a4"
down_revision = "a7b8c9d0e2f3"
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
