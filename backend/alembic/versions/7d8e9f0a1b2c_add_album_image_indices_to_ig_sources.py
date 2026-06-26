"""add album_image_indices to ig_sources

Revision ID: 7d8e9f0a1b2c
Revises: 6c7d8e9f0a1b
Create Date: 2026-06-27

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "7d8e9f0a1b2c"
down_revision = "6c7d8e9f0a1b"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "ig_sources",
        sa.Column(
            "album_image_indices",
            postgresql.ARRAY(sa.Integer()),
            server_default="{1}",
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column("ig_sources", "album_image_indices")
