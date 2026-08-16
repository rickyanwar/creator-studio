"""Discussion Card: never truncate headline; recalibrate readable font floor

Two fixes from testing 40 real subjects (2026-08-16):

1. titlePreferredMinFontSize was 90, picked without empirical verification.
   Real ~80-90 char AI questions (the copywriter's own cap) couldn't fit 3
   lines in this box even at 90px, so legitimate content was hitting the
   "fallback" tier meant only for unrealistic edge cases — e.g. "Will Carlos
   Sainz be remembered as a true Formula 1..." got clipped at a real 79
   chars. Recalibrated to 55, verified empirically to fit an 84-char
   question in 3 full lines with room to spare, still clearly legible.

2. The renderer's own truncation-as-fallback (added earlier the same day)
   is removed per explicit user correction: full text must always display —
   never cut words. If text still doesn't fit at the readable floor, the
   font keeps shrinking (down to the absolute floor) instead of the text
   being cut. That's a renderer code change (inject.js), not data, so it
   applies automatically on next renderer deploy — this migration only
   carries the recalibrated titlePreferredMinFontSize into the templates.

Revision ID: e5f6a7b8c9d1
Revises: d4e5f6a7b8c0
Create Date: 2026-08-16
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d1"
down_revision = "d4e5f6a7b8c0"
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
