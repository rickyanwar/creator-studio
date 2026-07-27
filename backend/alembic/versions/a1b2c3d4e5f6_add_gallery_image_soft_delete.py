"""add is_deleted/deleted_at to gallery_images (soft delete keeps source_image_url
in the dedup set so a deleted photo's URL is never re-downloaded)

Revision ID: a1b2c3d4e5f6
Revises: e7f8a9b0c1d2
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "e7f8a9b0c1d2"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "gallery_images",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("gallery_images", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_gallery_images_is_deleted", "gallery_images", ["is_deleted"])


def downgrade():
    op.drop_index("ix_gallery_images_is_deleted", table_name="gallery_images")
    op.drop_column("gallery_images", "deleted_at")
    op.drop_column("gallery_images", "is_deleted")
