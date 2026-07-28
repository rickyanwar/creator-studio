"""add scraper_relays to settings (relay pool fallback for news scraper)

Revision ID: a8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("settings", sa.Column("scraper_relays", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("settings", "scraper_relays")
