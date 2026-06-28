"""add last_crawl_error to ig_sources

Revision ID: b1c2d3e4f5a6
Revises: a0b1c2d3e4f5
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa

revision = "b1c2d3e4f5a6"
down_revision = "a0b1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        "ALTER TABLE ig_sources ADD COLUMN IF NOT EXISTS last_crawl_error VARCHAR(512)"
    )


def downgrade():
    op.drop_column("ig_sources", "last_crawl_error")
