"""add image edit + watermark fields

Revision ID: c2d3e4f5a6b7
Revises: b1c2d3e4f5a6
Create Date: 2026-07-04

"""
from alembic import op
import sqlalchemy as sa

revision = "c2d3e4f5a6b7"
down_revision = "b1c2d3e4f5a6"
branch_labels = None
depends_on = None


def upgrade():
    # ig_sources: cleanup toggle + optional custom prompt
    op.add_column(
        "ig_sources",
        sa.Column("image_edit_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "ig_sources",
        sa.Column("image_edit_custom_prompt", sa.Text(), nullable=True),
    )

    # target_fanpages: per-fanpage watermark text
    op.add_column(
        "target_fanpages",
        sa.Column("watermark_text", sa.String(length=128), nullable=True),
    )

    # posts.status: new 'editing_image' value between crawled and stored
    op.execute("ALTER TYPE poststatus ADD VALUE IF NOT EXISTS 'editing_image' BEFORE 'stored'")

    # publish_jobs.status: new 'pending_watermark' value before pending_caption
    op.execute("ALTER TYPE publishjobstatus ADD VALUE IF NOT EXISTS 'pending_watermark' BEFORE 'pending_caption'")

    # publish_jobs: per-job watermarked image variant
    op.add_column(
        "publish_jobs",
        sa.Column("watermarked_image_urls", sa.ARRAY(sa.String()), nullable=True),
    )


def downgrade():
    op.drop_column("publish_jobs", "watermarked_image_urls")
    op.drop_column("target_fanpages", "watermark_text")
    op.drop_column("ig_sources", "image_edit_custom_prompt")
    op.drop_column("ig_sources", "image_edit_enabled")
    # Note: Postgres does not support removing enum values; 'editing_image' and
    # 'pending_watermark' remain in the type on downgrade (harmless no-op values).
