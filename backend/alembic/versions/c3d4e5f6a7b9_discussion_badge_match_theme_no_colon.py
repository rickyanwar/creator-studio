"""Discussion Card: HOT TAKE badge matches theme color, drop trailing colon

Per user direction (2026-08-16): the HOT TAKE badge was still a fixed dark
red (#b3211b) regardless of the template's own highlight color, while only
the DISCUSSION badge matched — user clarified both labels should match the
template's theme color, not just one. Also: drop the auto-appended trailing
colon ("DISCUSSION:" / "HOT TAKE:") — just "DISCUSSION" / "HOT TAKE".

Re-applies all three Discussion Card template_json from default_templates.json
with hotFill = discussionFill and hotColor = discussionColor per template, and
"text": "DISCUSSION" (no colon) as the seed default. The colon-stripping
itself is a renderer code change (inject.js), not a data change, so it takes
effect on the next renderer deploy regardless of this migration.

Revision ID: c3d4e5f6a7b9
Revises: b2c3d4e5f6a8
Create Date: 2026-08-16
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b9"
down_revision = "b2c3d4e5f6a8"
branch_labels = None
depends_on = None

_NAMES = [
    "Discussion Card — Red · Center",
    "Discussion Card — Yellow · Center",
    "Discussion Card — Green · Center",
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
