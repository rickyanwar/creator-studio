"""add scraper_mode and flashapi_api_key to settings

Revision ID: 9f0a1b2c3d4e
Revises: 8e9f0a1b2c3d
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa

revision = "9f0a1b2c3d4e"
down_revision = "8e9f0a1b2c3d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "settings",
        sa.Column("scraper_mode", sa.String(32), nullable=False, server_default="auto"),
    )
    op.add_column(
        "settings",
        sa.Column("flashapi_api_key_encrypted", sa.String(512), nullable=True),
    )


def downgrade():
    op.drop_column("settings", "flashapi_api_key_encrypted")
    op.drop_column("settings", "scraper_mode")
