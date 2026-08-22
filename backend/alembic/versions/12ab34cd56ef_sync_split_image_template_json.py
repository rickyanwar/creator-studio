"""sync split-image (image_2 slot) template_json into design_templates rows

Every render this session that "used the real template" actually read
default_templates.json directly as a file — the actual design_templates
DB rows (local and production) were never updated, so they still had the
old single-photo layout (image slot = 1080x1350, no image_2, no scrim
compression). This migration copies the validated split-capable
template_json (image_2 slot added, photo_h = scrim.top + 80, scrim
gradient compressed to reach full opacity at the photo edge) from the
seed file into the 15 already-existing rows that have it, by name.

Revision ID: 12ab34cd56ef
Revises: 11fb01729ede
Create Date: 2026-08-22
"""

import json

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "12ab34cd56ef"
down_revision = "11fb01729ede"
branch_labels = None
depends_on = None


def upgrade():
    from app.seeds import load_default_templates

    templates_by_name = {t.get("name"): t for t in load_default_templates()}
    conn = op.get_bind()
    updated = 0
    for tpl in templates_by_name.values():
        objects = tpl.get("template_json", {}).get("objects", [])
        if not any(o.get("placeholderRole") == "image_2" for o in objects):
            continue
        result = conn.execute(
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
                "name": tpl["name"],
            },
        )
        if result.rowcount:
            updated += 1
    print(f"[sync] updated {updated} design_templates row(s) with split-image template_json")


def downgrade():
    # Old single-photo template_json is not retained in this migration —
    # restoring it means re-running the prior seed migrations or a DB backup.
    pass
