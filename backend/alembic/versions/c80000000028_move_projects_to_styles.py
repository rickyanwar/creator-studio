"""move_projects_to_styles

Revision ID: c80000000028
Revises: c80000000027
"""

import json
from alembic import op
import sqlalchemy as sa
from sqlalchemy import text
from app.services.template_styles import plan_brand_move

revision = "c80000000028"
down_revision = "c80000000027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Backup target_fanpages
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS brand_move_bak_c28_fanpages AS
            SELECT id, default_news_template_id, default_quote_template_id,
                   default_discussion_template_id, accent_color, template_style,
                   brand_override, accent_secondary_color, quote_mark_color
            FROM target_fanpages
            """
        )
    )

    # 2. Backup publish_jobs
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS brand_move_bak_c28_jobs AS
            SELECT id, design_template_id
            FROM publish_jobs
            WHERE status IN ('pending_caption', 'pending_design', 'rendering')
              AND design_template_id IS NOT NULL
            """
        )
    )

    # 3. Load fanpages, templates, queued jobs
    fp_rows = conn.execute(
        text(
            """
            SELECT id, default_news_template_id, default_quote_template_id,
                   default_discussion_template_id, accent_color
            FROM target_fanpages
            """
        )
    ).fetchall()
    fanpages = [
        {
            "id": r[0],
            "default_news_template_id": r[1],
            "default_quote_template_id": r[2],
            "default_discussion_template_id": r[3],
            "accent_color": r[4],
        }
        for r in fp_rows
    ]

    tpl_rows = conn.execute(
        text(
            """
            SELECT id, name, category, fanpage_id, template_json
            FROM design_templates
            """
        )
    ).fetchall()
    templates = []
    for r in tpl_rows:
        tj = r[4]
        if isinstance(tj, str):
            tj = json.loads(tj)
        templates.append({
            "id": r[0],
            "name": r[1],
            "category": r[2],
            "fanpage_id": r[3],
            "template_json": tj,
        })

    job_rows = conn.execute(
        text(
            """
            SELECT id, fanpage_id, design_template_id
            FROM publish_jobs
            WHERE status IN ('pending_caption', 'pending_design', 'rendering')
              AND design_template_id IS NOT NULL
            """
        )
    ).fetchall()
    jobs = [
        {
            "id": r[0],
            "fanpage_id": r[1],
            "design_template_id": r[2],
        }
        for r in job_rows
    ]

    # 4. Plan and execute updates
    plan = plan_brand_move(fanpages, templates, jobs)

    for fp_dict in plan["fanpages"]:
        conn.execute(
            text(
                """
                UPDATE target_fanpages
                SET template_style = :template_style,
                    default_news_template_id = :default_news_template_id,
                    default_quote_template_id = :default_quote_template_id,
                    default_discussion_template_id = :default_discussion_template_id,
                    brand_override = :brand_override,
                    accent_color = :accent_color,
                    accent_secondary_color = :accent_secondary_color,
                    quote_mark_color = :quote_mark_color
                WHERE id = :id
                """
            ),
            fp_dict,
        )

    for job_dict in plan["jobs"]:
        conn.execute(
            text("UPDATE publish_jobs SET design_template_id = :t WHERE id = :id"),
            {"t": job_dict["design_template_id"], "id": job_dict["id"]},
        )

    # 5. Summary
    print(f"[c28] moved {len(plan['fanpages'])} fan page(s), remapped {len(plan['jobs'])} queued job(s)")


def downgrade() -> None:
    conn = op.get_bind()

    conn.execute(
        text(
            """
            UPDATE target_fanpages tf
            SET default_news_template_id = bak.default_news_template_id,
                default_quote_template_id = bak.default_quote_template_id,
                default_discussion_template_id = bak.default_discussion_template_id,
                accent_color = bak.accent_color,
                template_style = bak.template_style,
                brand_override = bak.brand_override,
                accent_secondary_color = bak.accent_secondary_color,
                quote_mark_color = bak.quote_mark_color
            FROM brand_move_bak_c28_fanpages bak
            WHERE tf.id = bak.id
            """
        )
    )

    conn.execute(
        text(
            """
            UPDATE publish_jobs pj
            SET design_template_id = bak.design_template_id
            FROM brand_move_bak_c28_jobs bak
            WHERE pj.id = bak.id
            """
        )
    )

    conn.execute(text("DROP TABLE IF EXISTS brand_move_bak_c28_jobs"))
    conn.execute(text("DROP TABLE IF EXISTS brand_move_bak_c28_fanpages"))
