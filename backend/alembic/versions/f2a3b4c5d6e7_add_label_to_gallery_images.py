"""add vision label to gallery_images

Each gallery image is labelled at download ("face" | "action" | "other") so the
designer can pick the right photo style per news context.

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f2a3b4c5d6e7"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_images", sa.Column("label", sa.String(length=16), nullable=True))
    op.create_index("ix_gallery_images_label", "gallery_images", ["label"])


def downgrade():
    op.drop_index("ix_gallery_images_label", table_name="gallery_images")
    op.drop_column("gallery_images", "label")
