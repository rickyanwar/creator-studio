"""brand_look_columns

Revision ID: c80000000026
Revises: c80000000025
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000026"
down_revision = "c80000000025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("template_style", sa.String(length=16), nullable=True))
    op.add_column("target_fanpages", sa.Column("brand_override", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("target_fanpages", sa.Column("accent_secondary_color", sa.String(length=9), nullable=True))
    op.add_column("target_fanpages", sa.Column("badge_color", sa.String(length=9), nullable=True))
    op.add_column("target_fanpages", sa.Column("badge_text_color", sa.String(length=9), nullable=True))
    op.add_column("target_fanpages", sa.Column("quote_mark_color", sa.String(length=9), nullable=True))
    op.add_column("target_fanpages", sa.Column("quote_streak", sa.Boolean(), nullable=True))
    op.add_column("design_templates", sa.Column("style", sa.String(length=16), nullable=True))
    op.add_column("design_templates", sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="false"))
    op.create_index("ix_design_templates_style", "design_templates", ["style"])


def downgrade() -> None:
    op.drop_index("ix_design_templates_style", table_name="design_templates")
    op.drop_column("design_templates", "is_hidden")
    op.drop_column("design_templates", "style")
    op.drop_column("target_fanpages", "quote_streak")
    op.drop_column("target_fanpages", "quote_mark_color")
    op.drop_column("target_fanpages", "badge_text_color")
    op.drop_column("target_fanpages", "badge_color")
    op.drop_column("target_fanpages", "accent_secondary_color")
    op.drop_column("target_fanpages", "brand_override")
    op.drop_column("target_fanpages", "template_style")
