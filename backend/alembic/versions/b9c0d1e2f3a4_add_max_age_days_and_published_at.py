"""add max_age_days to news_sources + article_published_at to scraped_articles

Revision ID: b9c0d1e2f3a4
Revises: a8b9c0d1e2f3
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b9c0d1e2f3a4"
down_revision = "a8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "news_sources",
        sa.Column("max_age_days", sa.Integer(), nullable=False, server_default="3"),
    )
    op.add_column(
        "scraped_articles",
        sa.Column("article_published_at", sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column("scraped_articles", "article_published_at")
    op.drop_column("news_sources", "max_age_days")
