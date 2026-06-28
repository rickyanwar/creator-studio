"""add max_post_age_days to settings

Revision ID: a0b1c2d3e4f5
Revises: 9f0a1b2c3d4e
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa

revision = "a0b1c2d3e4f5"
down_revision = "9f0a1b2c3d4e"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE settings ADD COLUMN IF NOT EXISTS max_post_age_days INTEGER NOT NULL DEFAULT 2"
    )


def downgrade():
    op.drop_column("settings", "max_post_age_days")
    