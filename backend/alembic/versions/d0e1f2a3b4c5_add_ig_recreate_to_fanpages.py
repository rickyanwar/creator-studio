"""add Mode 3 IG-recreate fields to target_fanpages

Per-fanpage IG content recreate: classify each IG post image (9Router vision)
into quote/news/other and rebuild it on a quote or news template.

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d0e1f2a3b4c5"
down_revision = "c9d0e1f2a3b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("ig_recreate_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("target_fanpages", sa.Column("ig_recreate_quote_template_id", sa.Integer(), nullable=True))
    op.add_column("target_fanpages", sa.Column("ig_recreate_news_template_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("target_fanpages", "ig_recreate_news_template_id")
    op.drop_column("target_fanpages", "ig_recreate_quote_template_id")
    op.drop_column("target_fanpages", "ig_recreate_enabled")
