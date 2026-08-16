"""seed Discussion Card Yellow/Green center variants + rename+recolor Red one

Mode 4 previously had a single discussion template (based on the "News
Highlight — Red · Center" layout) whose badge was a mismatched yellow
(#f2c300) against a red headline accent. Per user direction (2026-08-16):
copy the "text center" news templates into Mode 4 discussion variants, one
per highlight color, each with the discussion badge color matched to that
template's own highlight/accent color — not a fixed yellow regardless of
theme.

This migration:
  1. Renames the existing "Discussion Card (badge + big question)" row to
     "Discussion Card — Red · Center" and recolors its badge to match its own
     red accent (#ec3324) instead of the old mismatched yellow.
  2. Seeds two new rows — "Discussion Card — Yellow · Center" (#ffcb2b) and
     "Discussion Card — Green · Center" (#5af905) — copied from their
     matching "News Highlight — {Color} · Center" news templates.
The rename happens BEFORE seed_default_templates() runs so that idempotent
insert doesn't see the new Red name as "missing" and create a duplicate row.

Revision ID: a1b2c3d4e5f7
Revises: e9f0a1b2c3d4
Create Date: 2026-08-16
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f7"
down_revision = "e9f0a1b2c3d4"
branch_labels = None
depends_on = None

_OLD_RED_NAME = "Discussion Card (badge + big question)"
_NEW_NAMES = [
    "Discussion Card — Red · Center",
    "Discussion Card — Yellow · Center",
    "Discussion Card — Green · Center",
]


def upgrade():
    from app.seeds import load_default_templates, seed_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}

    red = templates_by_name.get("Discussion Card — Red · Center")
    if not red:
        print("[warn] 'Discussion Card — Red · Center' not found in default_templates.json — skipping rename")
    else:
        op.get_bind().execute(
            text(
                """
                UPDATE design_templates
                   SET name = :new_name,
                       template_json = CAST(:tjson AS jsonb),
                       placeholder_config = CAST(:pconf AS jsonb),
                       updated_at = now()
                 WHERE name = :old_name AND fanpage_id IS NULL
                """
            ),
            {
                "new_name": red["name"],
                "tjson": json.dumps(red.get("template_json")),
                "pconf": json.dumps(red.get("placeholder_config")),
                "old_name": _OLD_RED_NAME,
            },
        )

    # Inserts whatever's still missing by name (idempotent) — at this point
    # that's just the two new Yellow/Green rows; Red was just renamed above
    # so seed_default_templates() will see it as already present.
    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")

    for name in _NEW_NAMES:
        op.get_bind().execute(
            text("UPDATE design_templates SET category = 'discussion' WHERE name = :n AND fanpage_id IS NULL"),
            {"n": name},
        )


def downgrade():
    for name in ["Discussion Card — Yellow · Center", "Discussion Card — Green · Center"]:
        op.get_bind().execute(
            text("DELETE FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
            {"n": name},
        )
    op.get_bind().execute(
        text("UPDATE design_templates SET name = :old_name WHERE name = :new_name AND fanpage_id IS NULL"),
        {"old_name": _OLD_RED_NAME, "new_name": "Discussion Card — Red · Center"},
    )
