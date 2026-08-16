"""update Discussion Card template to the News Highlight — Red · Center base

Re-writes the seeded "Discussion Card (badge + big question)" template_json to
the Red·Center layout (full photo + bottom scrim + centered big headline) plus
the DISCUSSION/HOT TAKE label badge. seed_default_templates only inserts when
missing, so an existing install needs this explicit UPDATE. Reads the canonical
JSON from default_templates.json so it stays the single source of truth.

Revision ID: c4f5a6b7d8e9
Revises: c3e4f5a6b7d8
Create Date: 2026-08-09
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "c4f5a6b7d8e9"
down_revision = "c3e4f5a6b7d8"
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
                   canvas_width = :cw,
                   canvas_height = :ch,
                   category = 'discussion',
                   updated_at = now()
             WHERE name = :name AND fanpage_id IS NULL
            """
        ),
        {
            "tjson": json.dumps(tpl.get("template_json")),
            "pconf": json.dumps(tpl.get("placeholder_config")),
            "cw": tpl.get("canvas_width", 1080),
            "ch": tpl.get("canvas_height", 1350),
            "name": _NAME,
        },
    )


def downgrade():
    # No-op: the previous template_json isn't retained. The template row stays.
    pass
