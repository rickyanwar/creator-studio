"""darken Quote Card — Green · Center's name badge text for contrast

User feedback (2026-09-01), right after the name badge from 6170ed96edbb
shipped: white text on the bright lime green (#5af905) badge reads poorly.
Discussion Card — Green · Center already solved this exact problem for its
own badge (discussionColor/hotColor #111111 instead of white) — reused the
same near-black value here for consistency, subtitle.fill #ffffff ->
#111111 (subtitle has no separate color-override property like label
does; the object's own `fill` IS what renders for the non-accent-marked
portion of the text — confirmed against inject.js before changing this).

Revision ID: 14bb7a54fe53
Revises: 6170ed96edbb
Create Date: 2026-09-01
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "14bb7a54fe53"
down_revision = "6170ed96edbb"
branch_labels = None
depends_on = None

_NAME = "Quote Card — Green · Center"


def upgrade():
    from app.seeds import load_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}
    tpl = templates_by_name.get(_NAME)
    if not tpl:
        print(f"[warn] {_NAME!r} not found in default_templates.json — skipping")
        return
    op.get_bind().execute(
        text(
            """
            UPDATE design_templates
               SET template_json = CAST(:tjson AS jsonb),
                   updated_at = now()
             WHERE name = :name AND fanpage_id IS NULL
            """
        ),
        {"tjson": json.dumps(tpl.get("template_json")), "name": _NAME},
    )


def downgrade():
    op.get_bind().execute(
        text(
            """
            UPDATE design_templates
               SET template_json = jsonb_set(template_json, '{objects,6,fill}', '"#ffffff"'),
                   updated_at = now()
             WHERE name = :name AND fanpage_id IS NULL
            """
        ),
        {"name": _NAME},
    )
