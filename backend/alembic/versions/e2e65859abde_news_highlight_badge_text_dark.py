"""darken the News badge text on 5 News Highlight templates for contrast

User request (2026-09-05): white text on the green/yellow News badge pill
reads poorly, same class of problem 14bb7a54fe53 already fixed for Quote
Card — Green · Center's name badge. Reused the same near-black #111111
value for consistency. Red · Left/Center were left alone — not part of
the request, and white-on-red already has good contrast.

Revision ID: e2e65859abde
Revises: 14bb7a54fe53
Create Date: 2026-09-05
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "e2e65859abde"
down_revision = "14bb7a54fe53"
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
                                        THEN jsonb_set(obj, '{fill}', '"#ffffff"')
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
