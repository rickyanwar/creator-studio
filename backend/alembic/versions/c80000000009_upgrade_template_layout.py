"""upgrade design_templates layout

Revision ID: c80000000009
Revises: c80000000008
Create Date: 2026-10-12
"""

import json
import logging
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text

from app.services.template_layout_upgrade import upgrade_template_json

# revision identifiers, used by Alembic.
revision = "c80000000009"
down_revision = "c80000000008"
branch_labels = None
depends_on = None

logger = logging.getLogger(__name__)


def upgrade():
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, name, category, template_json FROM design_templates")
    ).fetchall()

    updated_count = 0
    for r in rows:
        row_id, name, category, tj = r[0], r[1], r[2], r[3]
        if not tj or not isinstance(tj, dict):
            continue

        upgraded = upgrade_template_json(tj, category=category)
        if upgraded != tj:
            conn.execute(
                text("UPDATE design_templates SET template_json = CAST(:tjson AS jsonb) WHERE id = :id"),
                {"tjson": json.dumps(upgraded), "id": row_id},
            )
            updated_count += 1

    print(f"[migration c80000000009] upgraded layout for {updated_count} design_templates rows")


def downgrade():
    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, name, category, template_json FROM design_templates")
    ).fetchall()

    reverted_count = 0
    for r in rows:
        row_id, name, category, tj = r[0], r[1], r[2], r[3]
        if not tj or not isinstance(tj, dict):
            continue

        objects = tj.get("objects", [])
        if not isinstance(objects, list):
            continue

        changed = False
        new_objects = []
        for obj in objects:
            if not isinstance(obj, dict):
                new_objects.append(obj)
                continue
            new_obj = dict(obj)
            role = new_obj.get("placeholderRole")
            if role == "title":
                # Compute what the helper would compute
                dummy_tj = {"objects": [dict(obj)]}
                upgraded_dummy = upgrade_template_json(dummy_tj, category=category)
                up_title = upgraded_dummy["objects"][0]
                for k in ("titleGrowMaxHeight", "titleMinReadableFontSize", "titleFitChars"):
                    if k in new_obj and k in up_title and new_obj[k] == up_title[k]:
                        del new_obj[k]
                        changed = True
            elif role == "subtitle":
                dummy_tj = {"objects": [{"placeholderRole": "title"}, dict(obj)]}
                upgraded_dummy = upgrade_template_json(dummy_tj, category=category)
                up_sub = upgraded_dummy["objects"][1]
                for k in ("subtitleMinFontSize", "subtitleMaxLines"):
                    if k in new_obj and k in up_sub and new_obj[k] == up_sub[k]:
                        del new_obj[k]
                        changed = True
            elif role == "caption":
                dummy_tj = {"objects": [{"placeholderRole": "title"}, dict(obj)]}
                upgraded_dummy = upgrade_template_json(dummy_tj, category=category)
                up_cap = upgraded_dummy["objects"][1]
                for k in ("captionMinFontSize", "captionMaxLines"):
                    if k in new_obj and k in up_cap and new_obj[k] == up_cap[k]:
                        del new_obj[k]
                        changed = True
            new_objects.append(new_obj)

        if changed:
            new_tj = dict(tj)
            new_tj["objects"] = new_objects
            conn.execute(
                text("UPDATE design_templates SET template_json = CAST(:tjson AS jsonb) WHERE id = :id"),
                {"tjson": json.dumps(new_tj), "id": row_id},
            )
            reverted_count += 1

    print(f"[migration c80000000009] downgraded layout for {reverted_count} design_templates rows")
