"""add Mode 6 Facebook photo fields to fanpages + facebook_photo_sources +
facebook_photo_ideas tables

Mode 6: clone another Facebook page's (not this fanpage's own) `/photos`
gallery. New photos (dedup by fbid via GalleryImage.source_image_url,
source_engine="facebook_photo") are classified via vision into
news/discussion/other at topup time and staged as FacebookPhotoIdea rows,
consumed FIFO on the fanpage's own pacing into a PublishJob that renders
directly from the bound GalleryImage (no article, no photo re-search) — see
app/tasks/facebook_photo.py. No like-count/growth gate in this version.

Revision ID: a80ac9d83f8d
Revises: d33238f4b363
Create Date: 2026-09-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a80ac9d83f8d"
down_revision = "d33238f4b363"
branch_labels = None
depends_on = None


def upgrade():
    # ── target_fanpages: Mode 6 config ──
    op.add_column(
        "target_fanpages",
        sa.Column("facebook_photo_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    publishmode = postgresql.ENUM("auto", "manual_review", name="publishmode", create_type=False)
    op.add_column(
        "target_fanpages",
        sa.Column("facebook_photo_publish_mode", publishmode, nullable=False, server_default="manual_review"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("facebook_photo_daily_count", sa.Integer(), nullable=False, server_default="2"),
    )

    # ── facebook_photo_sources: curated pages to monitor ──
    op.create_table(
        "facebook_photo_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("page_url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_facebook_photo_sources_fanpage_id", "facebook_photo_sources", ["fanpage_id"])
    op.create_index("ix_facebook_photo_sources_last_used_at", "facebook_photo_sources", ["last_used_at"])

    # ── facebook_photo_ideas: the classified staging queue ──
    op.create_table(
        "facebook_photo_ideas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("gallery_image_id", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("design_title", sa.Text(), nullable=False),
        sa.Column("design_subtitle", sa.Text(), nullable=True),
        sa.Column("design_caption", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gallery_image_id"], ["gallery_images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_facebook_photo_ideas_fanpage_id", "facebook_photo_ideas", ["fanpage_id"])
    op.create_index("ix_facebook_photo_ideas_gallery_image_id", "facebook_photo_ideas", ["gallery_image_id"])
    op.create_index("ix_facebook_photo_ideas_status", "facebook_photo_ideas", ["status"])
    op.create_index("ix_facebook_photo_ideas_created_at", "facebook_photo_ideas", ["created_at"])


def downgrade():
    op.drop_index("ix_facebook_photo_ideas_created_at", table_name="facebook_photo_ideas")
    op.drop_index("ix_facebook_photo_ideas_status", table_name="facebook_photo_ideas")
    op.drop_index("ix_facebook_photo_ideas_gallery_image_id", table_name="facebook_photo_ideas")
    op.drop_index("ix_facebook_photo_ideas_fanpage_id", table_name="facebook_photo_ideas")
    op.drop_table("facebook_photo_ideas")

    op.drop_index("ix_facebook_photo_sources_last_used_at", table_name="facebook_photo_sources")
    op.drop_index("ix_facebook_photo_sources_fanpage_id", table_name="facebook_photo_sources")
    op.drop_table("facebook_photo_sources")

    op.drop_column("target_fanpages", "facebook_photo_daily_count")
    op.drop_column("target_fanpages", "facebook_photo_publish_mode")
    op.drop_column("target_fanpages", "facebook_photo_enabled")
