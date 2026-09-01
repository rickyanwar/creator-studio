"""add mode2_badge_text to target_fanpages + labelBadge/label to News templates

User request (2026-09-01): bring the same small pill badge Discussion Card
already has (e.g. "HOT TAKE") to the News templates too (e.g. "F1 NEWS"),
per-fanpage customizable text, position/color matching each template's own
alignment/accent (see default_templates.json — News Highlight Red/Yellow/
Green x Left/Center + the Quote-overlay Green variant all got a labelBadge+
label pair inserted). "MotoGP News (two-tone headline)" deliberately
excluded — no scrim-role object, structurally different overlay, same
reason it was skipped for the earlier split-image work.

Revision ID: f6a1cecb127e
Revises: 2000777df01a
Create Date: 2026-09-01
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "f6a1cecb127e"
down_revision = "2000777df01a"
branch_labels = None
depends_on = None

_NEWS_NAMES = [
    "News Highlight — Red · Left",
    "News Highlight — Red · Center",
    "News Highlight — Yellow · Left",
    "News Highlight — Green · Left",
    "News Highlight — Yellow · Center",
    "News Highlight — Green · Center",
    "News Highlight — Green · Center (Quote overlay)",
]


def upgrade():
    op.get_bind().execute(text("ALTER TABLE target_fanpages ADD COLUMN mode2_badge_text VARCHAR(64)"))

    from app.seeds import load_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}
    for name in _NEWS_NAMES:
        tpl = templates_by_name.get(name)
        if not tpl:
            print(f"[warn] {name!r} not found in default_templates.json — skipping")
            continue
        op.get_bind().execute(
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
    op.get_bind().execute(text("ALTER TABLE target_fanpages DROP COLUMN mode2_badge_text"))
    # Template rows: not reverted (would need the pre-badge JSON snapshot,
    # not worth carrying around for a cosmetic addition) — re-run
    # seed_default_templates against an older default_templates.json
    # checkout if a full revert is ever actually needed.
