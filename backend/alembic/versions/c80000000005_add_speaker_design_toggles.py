"""add per-fanpage speaker design toggles

Revision ID: c80000000005
Revises: c80000000004
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000005"
down_revision = "c80000000004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_fanpages",
        sa.Column("news_visual_engine", sa.String(length=16), nullable=False, server_default="off"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("fb_visual_engine", sa.String(length=16), nullable=False, server_default="off"),
    )


def downgrade() -> None:
    op.drop_column("target_fanpages", "fb_visual_engine")
    op.drop_column("target_fanpages", "news_visual_engine")
