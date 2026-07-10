"""add scraper_proxies to settings

Newline-separated proxy pool for the news scraper (random per request).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e1"
down_revision = "e5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("settings", sa.Column("scraper_proxies", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("settings", "scraper_proxies")
