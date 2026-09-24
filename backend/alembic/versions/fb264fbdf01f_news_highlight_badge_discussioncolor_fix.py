"""fix News Highlight badge label discussionColor/hotColor (still white)

e2e65859abde (2026-09-05) darkened these 5 templates' badge label to
#111111 by setting the object's `fill` field — but the renderer
(renderer/inject.js, "Label badge" block, ~line 240) NEVER reads `fill`
for a placeholderRole="label" object when a real label value is sent
(true for every Mode 2 News render, since render_design always sends
`label=news_badge_text`). It always overwrites `fill` at render time from
`labelObj.discussionColor`/`hotColor` instead (fields meant for Mode 4
Discussion cards, which these News templates happen to also carry, still
set to the old white). Confirmed live against the DB (2026-09-25): all 5
templates had fill=#111111 but discussionColor=#111111... actually still
"#ffffff" — the migration only ever changed `fill`, discussionColor was
untouched. Fix here brings discussionColor/hotColor in line with the
already-correct fill value.

Revision ID: fb264fbdf01f
Revises: a80ac9d83f8d
Create Date: 2026-09-25
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "fb264fbdf01f"
down_revision = "a80ac9d83f8d"
branch_labels = None
depends_on = None

_NAMES = [
    "News Highlight — Green · Left",
    "News Highlight — Green · Center (Quote overlay)",
    "News Highlight — Yellow · Left",
    "News Highlight — Yellow · Center",
    "News Highlight — Green · Center",
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
                                        THEN obj || '{"discussionColor": "#ffffff", "hotColor": "#ffffff"}'::jsonb
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
