"""add last_used_at to gallery_images

Tracks WHEN an image was last picked for a render (separate from is_used,
which only tracks whether it's ever been used at all) so the auto-selection
pipeline can enforce a reuse cooldown — don't reuse an image within N days.

Revision ID: ea233a018626
Revises: f3a4b5c6d7e8
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ea233a018626"
down_revision = "f3a4b5c6d7e8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_images", sa.Column("last_used_at", sa.DateTime(), nullable=True))
    op.create_index("ix_gallery_images_last_used_at", "gallery_images", ["last_used_at"])


def downgrade():
    op.drop_index("ix_gallery_images_last_used_at", table_name="gallery_images")
    op.drop_column("gallery_images", "last_used_at")
