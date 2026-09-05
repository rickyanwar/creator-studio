"""align the News badge's left edge with the title on the Left variants

User report (2026-09-05), with a real published render attached: the
green "F1 NEWS" pill sits visibly left of "BREAKING..." below it, even
though label.left and title.left are both 56 in the template JSON.

Root cause traced in renderer/inject.js: for a non-centered label,
labelBadge.left is computed as `labelObj.left - padX` (padX =
label.labelPadX, 20 for these templates) — NOT read from the badge
object's own `left` in the JSON at all (same class of "JSON value is
ignored at render time" gotcha as the labelRadius issue found
2026-09-01). With label.left=56 and padX=20, the badge actually renders
at x=36, 20px left of the title's x=56 — confirmed via a real /render
call before and after this fix.

Fix: bump label.left from 56 to 76 (title.left + labelPadX) on both
"— Left" variants so the computed badge.left (76-20=56) lands exactly
on the title's left edge. The label TEXT keeps the same 20px padding
from its own badge's left edge either way — this only moves the
badge+text unit as a whole, not the internal spacing. Center variants
are unaffected (they centre the badge on the canvas, not against
title.left).

Revision ID: f165c0922101
Revises: e2e65859abde
Create Date: 2026-09-05
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "f165c0922101"
down_revision = "e2e65859abde"
branch_labels = None
depends_on = None

_NAMES = [
    "News Highlight — Green · Left",
    "News Highlight — Yellow · Left",
]


def upgrade():
    from app.seeds import load_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}
    conn = op.get_bind()
    for name in _NAMES:
        tpl = templates_by_name.get(name)
        if not tpl:
            print(f"[warn] {name!r} not found in default_templates.json — skipping")
            continue
        conn.execute(
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
    conn = op.get_bind()
    for name in _NAMES:
        conn.execute(
            text(
                """
                UPDATE design_templates
                   SET template_json = jsonb_set(
                           template_json,
                           '{objects}',
                           (
                               SELECT jsonb_agg(
                                   CASE WHEN obj->>'placeholderRole' = 'label'
                                        THEN jsonb_set(obj, '{left}', '56')
                                        ELSE obj
                                   END
                               )
                               FROM jsonb_array_elements(template_json->'objects') AS obj
                           )
                       ),
                       updated_at = now()
                 WHERE name = :name AND fanpage_id IS NULL
                """
            ),
            {"name": name},
        )
