"""add gallery labels attributes and settings

Revision ID: c80000000003
Revises: c80000000002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "c80000000003"
down_revision = "c80000000002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. gallery_images columns & GIN index
    op.add_column("gallery_images", sa.Column("attributes", JSONB, nullable=True))
    op.add_column("gallery_images", sa.Column("labeled_at", sa.DateTime(), nullable=True))
    op.add_column("gallery_images", sa.Column("label_error", sa.String(length=300), nullable=True))
    op.add_column("gallery_images", sa.Column("label_attempts", sa.SmallInteger(), nullable=False, server_default="0"))
    op.create_index("ix_gallery_images_attributes", "gallery_images", ["attributes"], postgresql_using="gin")

    # 2. settings columns
    op.add_column("settings", sa.Column("gallery_labeling_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("settings", sa.Column("gallery_labeling_daily_cap", sa.Integer(), nullable=False, server_default="1000"))
    op.add_column("settings", sa.Column("gallery_labeling_since", sa.DateTime(), nullable=True))


def downgrade() -> None:
    # Drop settings columns
    op.drop_column("settings", "gallery_labeling_since")
    op.drop_column("settings", "gallery_labeling_daily_cap")
    op.drop_column("settings", "gallery_labeling_enabled")

    # Drop gallery_images index & columns
    op.drop_index("ix_gallery_images_attributes", table_name="gallery_images")
    op.drop_column("gallery_images", "label_attempts")
    op.drop_column("gallery_images", "label_error")
    op.drop_column("gallery_images", "labeled_at")
    op.drop_column("gallery_images", "attributes")
