"""add Mode 5 Pinterest fields to fanpages + pinterest_sources +
pinterest_content_ideas tables + publish_jobs.source_gallery_image_id

Mode 5: photo-seeded content. Candidates pulled from Pinterest (AI-keyword
search and/or curated profile/board references) become PinterestContentIdea
rows in an editable queue, consumed FIFO on the fanpage's own pacing into a
PublishJob that renders directly from the bound GalleryImage (no article, no
photo re-search) — see app/tasks/pinterest.py.

Revision ID: 3136ab873a6d
Revises: cc4cb88f501b
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "3136ab873a6d"
down_revision = "cc4cb88f501b"
branch_labels = None
depends_on = None


def upgrade():
    # ── target_fanpages: Mode 5 config ──
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
    publishmode = postgresql.ENUM("auto", "manual_review", name="publishmode", create_type=False)
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_publish_mode", publishmode, nullable=False, server_default="manual_review"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_daily_count", sa.Integer(), nullable=False, server_default="2"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_source_mode", sa.String(length=16), nullable=False, server_default="both"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_render_style", sa.String(length=16), nullable=False, server_default="news"),
    )

    # ── pinterest_sources: curated profile/board references ──
    op.create_table(
        "pinterest_sources",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("times_used", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pinterest_sources_fanpage_id", "pinterest_sources", ["fanpage_id"])
    op.create_index("ix_pinterest_sources_last_used_at", "pinterest_sources", ["last_used_at"])

    # ── pinterest_content_ideas: the editable staging queue ──
    op.create_table(
        "pinterest_content_ideas",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("fanpage_id", sa.Integer(), nullable=False),
        sa.Column("gallery_image_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["fanpage_id"], ["target_fanpages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["gallery_image_id"], ["gallery_images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pinterest_content_ideas_fanpage_id", "pinterest_content_ideas", ["fanpage_id"])
    op.create_index("ix_pinterest_content_ideas_gallery_image_id", "pinterest_content_ideas", ["gallery_image_id"])
    op.create_index("ix_pinterest_content_ideas_status", "pinterest_content_ideas", ["status"])
    op.create_index("ix_pinterest_content_ideas_created_at", "pinterest_content_ideas", ["created_at"])

    # ── publish_jobs: bind a job directly to its Mode 5 source photo ──
    op.add_column(
        "publish_jobs",
        sa.Column("source_gallery_image_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_publish_jobs_source_gallery_image_id",
        "publish_jobs",
        "gallery_images",
        ["source_gallery_image_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_publish_jobs_source_gallery_image_id", "publish_jobs", ["source_gallery_image_id"]
    )


def downgrade():
    op.drop_index("ix_publish_jobs_source_gallery_image_id", table_name="publish_jobs")
    op.drop_constraint("fk_publish_jobs_source_gallery_image_id", "publish_jobs", type_="foreignkey")
    op.drop_column("publish_jobs", "source_gallery_image_id")

    op.drop_index("ix_pinterest_content_ideas_created_at", table_name="pinterest_content_ideas")
    op.drop_index("ix_pinterest_content_ideas_status", table_name="pinterest_content_ideas")
    op.drop_index("ix_pinterest_content_ideas_gallery_image_id", table_name="pinterest_content_ideas")
    op.drop_index("ix_pinterest_content_ideas_fanpage_id", table_name="pinterest_content_ideas")
    op.drop_table("pinterest_content_ideas")

    op.drop_index("ix_pinterest_sources_last_used_at", table_name="pinterest_sources")
    op.drop_index("ix_pinterest_sources_fanpage_id", table_name="pinterest_sources")
    op.drop_table("pinterest_sources")

    op.drop_column("target_fanpages", "pinterest_render_style")
    op.drop_column("target_fanpages", "pinterest_source_mode")
    op.drop_column("target_fanpages", "pinterest_daily_count")
    op.drop_column("target_fanpages", "pinterest_publish_mode")
    op.drop_column("target_fanpages", "pinterest_enabled")
