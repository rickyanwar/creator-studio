"""add name badge (subtitleBadge+subtitle) to Quote Card — Green · Center

User request (2026-09-01): this quote template was missing the "name
badge" (a small pill showing the speaker's name below the quote, e.g.
"GEORGE RUSSELL") that "Quote Card (name badge)" and "Quote Card
(word-highlight, name badge)" already have. Unlike the News-badge
labelBadge work earlier this session, subtitleBadge's rx/ry IS honored
as-is by renderer/inject.js (no labelRadius override needed there — only
labelBadge recomputes its own radius) — reused the exact geometry pattern
from "Quote Card (name badge)" (rx=ry=25 on a height=50 rect = a real
pill), centered under this template's own titleAnchorBottom=1270, colored
to match this template's own accent (#5af905, same as its quoteIcon/title
titleAccentColor). No backend/schema changes needed — design_renderer.py
already sends job.design_subtitle as "subtitle" in the render payload
regardless of template, so this is a pure template JSON addition.

Revision ID: 6170ed96edbb
Revises: f6a1cecb127e
Create Date: 2026-09-01
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "6170ed96edbb"
down_revision = "f6a1cecb127e"
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
               SET template_json = template_json #- '{objects,6}' #- '{objects,5}',
                   updated_at = now()
             WHERE name = :name AND fanpage_id IS NULL
            """
        ),
        {"name": _NAME},
    )
