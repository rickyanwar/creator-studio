"""add image_source_urls to posts

Revision ID: 4a1b2c3d4e5f
Revises: 3f50c924b58d
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY

revision = "4a1b2c3d4e5f"
down_revision = "3f50c924b58d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "posts",
        sa.Column(
            "image_source_urls",
            ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
    )


def downgrade():
    op.drop_column("posts", "image_source_urls")
