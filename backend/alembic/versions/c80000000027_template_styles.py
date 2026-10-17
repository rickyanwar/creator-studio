"""template_styles

Revision ID: c80000000027
Revises: c80000000026
"""

import json
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from app.services.template_styles import (
    STYLES,
    STYLE_TEMPLATE_NAMES,
    LEGACY_TEMPLATE_STYLE,
    upgrade_kept_template,
    make_discussion_template,
)

revision = "c80000000027"
down_revision = "c80000000026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Backup table
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS design_templates_bak_c27 AS
            SELECT id, template_json, is_default, category
            FROM design_templates
            WHERE fanpage_id IS NULL
            """
        )
    )

    # 2. Upgrade kept templates
    for style in STYLES:
        for cat in ("news", "quote"):
            name = STYLE_TEMPLATE_NAMES[style][cat]
            row = conn.execute(
                text(
                    "SELECT id, template_json FROM design_templates "
                    "WHERE name = :n AND fanpage_id IS NULL ORDER BY id LIMIT 1"
                ),
                {"n": name},
            ).fetchone()
            if not row:
                raise RuntimeError(f"c27: template not found: {name}")

            row_id, tj_raw = row[0], row[1]
            if isinstance(tj_raw, str):
                tj = json.loads(tj_raw)
            else:
                tj = tj_raw or {}

            new_tj = upgrade_kept_template(tj, style, cat)
            conn.execute(
                text(
                    """
                    UPDATE design_templates
                    SET template_json = CAST(:tjson AS jsonb),
                        style = :style,
                        category = :cat,
                        is_hidden = false
                    WHERE id = :id
                    """
                ),
                {
                    "tjson": json.dumps(new_tj),
                    "style": style,
                    "cat": cat,
                    "id": row_id,
                },
            )

    # 3. Create or update discussion templates
    for style in STYLES:
        news_name = STYLE_TEMPLATE_NAMES[style]["news"]
        row = conn.execute(
            text(
                "SELECT id, template_json, placeholder_config FROM design_templates "
                "WHERE name = :n AND fanpage_id IS NULL ORDER BY id LIMIT 1"
            ),
            {"n": news_name},
        ).fetchone()
        if not row:
            raise RuntimeError(f"c27: news template not found for discussion: {news_name}")

        news_id, news_tj_raw, news_pc_raw = row[0], row[1], row[2]
        if isinstance(news_tj_raw, str):
            news_tj = json.loads(news_tj_raw)
        else:
            news_tj = news_tj_raw or {}

        if isinstance(news_pc_raw, str):
            news_pc = json.loads(news_pc_raw)
        elif isinstance(news_pc_raw, dict):
            news_pc = dict(news_pc_raw)
        else:
            news_pc = {}
        news_pc["has_label"] = True

        disc_tj = make_discussion_template(news_tj, style)
        disc_name = STYLE_TEMPLATE_NAMES[style]["discussion"]

        existing_disc = conn.execute(
            text(
                "SELECT id FROM design_templates "
                "WHERE name = :n AND fanpage_id IS NULL ORDER BY id LIMIT 1"
            ),
            {"n": disc_name},
        ).fetchone()

        if existing_disc:
            conn.execute(
                text(
                    """
                    UPDATE design_templates
                    SET template_json = CAST(:tjson AS jsonb),
                        placeholder_config = CAST(:pc AS jsonb),
                        style = :style,
                        category = 'discussion',
                        is_hidden = false
                    WHERE id = :id
                    """
                ),
                {
                    "tjson": json.dumps(disc_tj),
                    "pc": json.dumps(news_pc),
                    "style": style,
                    "id": existing_disc[0],
                },
            )
        else:
            conn.execute(
                text(
                    """
                    INSERT INTO design_templates (
                        fanpage_id, name, template_json, placeholder_config,
                        canvas_width, canvas_height, is_default, category,
                        style, is_hidden, created_at, updated_at
                    ) VALUES (
                        NULL, :name, CAST(:tjson AS jsonb), CAST(:pc AS jsonb),
                        1080, 1350, false, 'discussion',
                        :style, false, now(), now()
                    )
                    """
                ),
                {
                    "name": disc_name,
                    "tjson": json.dumps(disc_tj),
                    "pc": json.dumps(news_pc),
                    "style": style,
                },
            )

    # 4. Set defaults
    conn.execute(text("UPDATE design_templates SET is_default = false WHERE fanpage_id IS NULL"))
    gp_names = list(STYLE_TEMPLATE_NAMES["gp"].values())
    conn.execute(
        text("UPDATE design_templates SET is_default = true WHERE fanpage_id IS NULL AND name IN (:a, :b, :c)"),
        {"a": gp_names[0], "b": gp_names[1], "c": gp_names[2]},
    )

    # 5. Hide legacy shared templates
    for lname in LEGACY_TEMPLATE_STYLE:
        conn.execute(
            text("UPDATE design_templates SET is_hidden = true WHERE fanpage_id IS NULL AND name = :name"),
            {"name": lname},
        )


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Restore template_json, is_default, category
    conn.execute(
        text(
            """
            UPDATE design_templates d
            SET template_json = b.template_json,
                is_default = b.is_default,
                category = b.category
            FROM design_templates_bak_c27 b
            WHERE d.id = b.id
            """
        )
    )

    # 2. Delete newly created discussion templates
    disc_names = [STYLE_TEMPLATE_NAMES[s]["discussion"] for s in STYLES]
    conn.execute(
        text(
            """
            DELETE FROM design_templates
            WHERE fanpage_id IS NULL
              AND name IN (:d1, :d2, :d3)
              AND id NOT IN (SELECT id FROM design_templates_bak_c27)
            """
        ),
        {"d1": disc_names[0], "d2": disc_names[1], "d3": disc_names[2]},
    )

    # 3. Reset style and is_hidden
    conn.execute(text("UPDATE design_templates SET style = NULL, is_hidden = false"))

    # 4. Drop backup table
    conn.execute(text("DROP TABLE IF EXISTS design_templates_bak_c27"))
