"""template text zone upgrade and backup

Revision ID: c80000000015
Revises: c80000000014
"""

import json
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from app.services.template_text_zone import apply_text_zone, apply_gp_design

revision = "c80000000015"
down_revision = "c80000000014"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    # 1. Create backup table design_templates_bak_c15 with id, template_json
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS design_templates_bak_c15 (
                id INTEGER PRIMARY KEY,
                template_json JSONB NOT NULL
            )
            """
        )
    )

    # 2. Copy current id, template_json of every row
    conn.execute(
        text(
            """
            INSERT INTO design_templates_bak_c15 (id, template_json)
            SELECT id, template_json FROM design_templates
            ON CONFLICT (id) DO UPDATE SET template_json = EXCLUDED.template_json
            """
        )
    )

    # 3. Apply helper to every design_templates row, updating only rows that changed
    rows = conn.execute(
        text("SELECT id, name, category, template_json FROM design_templates")
    ).fetchall()

    updated_count = 0
    for r in rows:
        row_id, name, category, tj = r[0], r[1], r[2], r[3]
        if not tj or not isinstance(tj, dict):
            continue

        updated = apply_text_zone(tj, zone_top=851, category=category)
        updated = apply_gp_design(updated, name=name)
        if updated != tj:
            conn.execute(
                text("UPDATE design_templates SET template_json = CAST(:tjson AS jsonb) WHERE id = :id"),
                {"tjson": json.dumps(updated), "id": row_id},
            )
            updated_count += 1

    print(f"[migration c80000000015] updated text zone for {updated_count} design_templates rows")


def downgrade():
    conn = op.get_bind()

    # 1. Restore template_json from backup table
    conn.execute(
        text(
            """
            UPDATE design_templates dt
            SET template_json = bak.template_json
            FROM design_templates_bak_c15 bak
            WHERE dt.id = bak.id
            """
        )
    )

    # 2. Drop backup table
    conn.execute(text("DROP TABLE IF EXISTS design_templates_bak_c15"))

    print("[migration c80000000015] restored template_json from design_templates_bak_c15 and dropped backup table")
