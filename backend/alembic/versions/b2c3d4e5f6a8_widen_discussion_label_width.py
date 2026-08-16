"""fix Discussion Card label textbox width (HOT TAKE: wraps to 2 lines)

Found while visually testing the new Yellow/Green center discussion variants
(2026-08-16): the "label" placeholder's initial width (320px) was narrow
enough that "HOT TAKE:" (two words) wrapped to 2 lines during the renderer's
initDimensions() call before the pill got resized to fit — "DISCUSSION:"
(one word) never showed the bug because Fabric can't wrap an unbreakable
single word, so it went unnoticed until a two-word label was actually
rendered. The 2-line label then overlapped the headline below it, since the
headline is positioned assuming a single-line label height above it.

Re-applies all three Discussion Card template_json from default_templates.json
with the label width widened 320 -> 700 (both words fit on one line at
fontSize 44 regardless of which label the copywriter picks; the renderer
still resizes the pill to the actual single-line text width afterward, so
this only prevents the premature wrap, it doesn't change final appearance).

Revision ID: b2c3d4e5f6a8
Revises: a1b2c3d4e5f7
Create Date: 2026-08-16
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a8"
down_revision = "a1b2c3d4e5f7"
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
