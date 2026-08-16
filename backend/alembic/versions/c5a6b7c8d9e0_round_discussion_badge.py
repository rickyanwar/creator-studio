"""refresh Discussion Card template (rounded label badge)

Re-applies the canonical "Discussion Card (badge + big question)" template_json
from default_templates.json — now with rounded label-badge corners (rx/ry). The
renderer also rounds the badge dynamically, so this mainly keeps the editor
preview / other environments in sync.

Revision ID: c5a6b7c8d9e0
Revises: c4f5a6b7d8e9
Create Date: 2026-08-09
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "c5a6b7c8d9e0"
down_revision = "c4f5a6b7d8e9"
branch_labels = None
depends_on = None

_NAME = "Discussion Card (badge + big question)"


def upgrade():
    from app.seeds import load_default_templates

    tpl = next((t for t in load_default_templates() if t.get("name") == _NAME), None)
    if not tpl:
        print(f"[warn] {_NAME!r} not found in default_templates.json — skipping")
        return

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
            "name": _NAME,
        },
    )


def downgrade():
    pass
