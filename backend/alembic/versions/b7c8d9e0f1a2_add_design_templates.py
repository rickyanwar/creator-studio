"""add design_templates table + template FKs (Phase 2D)

Revision ID: b7c8d9e0f1a2
Revises: a6b7c8d9e0f1
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "b7c8d9e0f1a2"
down_revision = "a6b7c8d9e0f1"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "design_templates",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("template_json", postgresql.JSONB(), nullable=True),
        sa.Column("placeholder_config", postgresql.JSONB(), nullable=True),
        sa.Column("canvas_width", sa.Integer(), nullable=False, server_default="1080"),
        sa.Column("canvas_height", sa.Integer(), nullable=False, server_default="1080"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_design_templates_fanpage_id", "design_templates", ["fanpage_id"])

    # FKs deferred from Phase 2C now that the table exists
    op.create_foreign_key(
        "fk_fanpages_mode2_default_template", "target_fanpages", "design_templates",
        ["mode2_default_template_id"], ["id"], ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_publish_jobs_design_template", "publish_jobs", "design_templates",
        ["design_template_id"], ["id"], ondelete="SET NULL",
    )


def downgrade():
    op.drop_constraint("fk_publish_jobs_design_template", "publish_jobs", type_="foreignkey")
    op.drop_constraint("fk_fanpages_mode2_default_template", "target_fanpages", type_="foreignkey")
    op.drop_table("design_templates")
