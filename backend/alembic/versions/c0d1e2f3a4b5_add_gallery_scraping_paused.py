"""add gallery_scraping_paused global switch to settings

Revision ID: c0d1e2f3a4b5
Revises: b9c0d1e2f3a4
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c0d1e2f3a4b5"
down_revision = "b9c0d1e2f3a4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "settings",
        sa.Column("gallery_scraping_paused", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("settings", "gallery_scraping_paused")
