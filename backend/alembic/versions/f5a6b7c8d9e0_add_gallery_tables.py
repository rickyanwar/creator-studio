"""add gallery tables (gallery_keywords, gallery_images)

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa

revision = "f5a6b7c8d9e0"
down_revision = "e4f5a6b7c8d9"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gallery_keywords",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("keyword", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("max_images", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("min_width", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("min_height", sa.Integer(), nullable=False, server_default="500"),
        sa.Column("source_engine", sa.String(16), nullable=False, server_default="bing"),
        sa.Column("license_filter", sa.String(64), nullable=False, server_default="commercial,modify"),
        sa.Column("last_downloaded_at", sa.DateTime(), nullable=True),
        sa.Column("last_download_error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_unique_constraint("uq_gallery_keywords_keyword", "gallery_keywords", ["keyword"])

    op.create_table(
        "gallery_images",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("keyword", sa.String(128), nullable=False),
        sa.Column("source_image_url", sa.String(1024), nullable=False),
        sa.Column("local_path", sa.String(512), nullable=False),
        sa.Column("public_url", sa.String(512), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("source_engine", sa.String(16), nullable=False),
        sa.Column("license_info", sa.String(64), nullable=True),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("downloaded_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_gallery_images_keyword", "gallery_images", ["keyword"])
    op.create_index("ix_gallery_images_is_used", "gallery_images", ["is_used"])
    op.create_unique_constraint("uq_gallery_images_source_image_url", "gallery_images", ["source_image_url"])


def downgrade():
    op.drop_table("gallery_images")
    op.drop_table("gallery_keywords")
