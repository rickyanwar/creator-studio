"""add Mode 2 (news content): fanpage mode2 fields, fanpage_news_sources, publish_jobs content_type

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "a6b7c8d9e0f1"
down_revision = "f5a6b7c8d9e0"
branch_labels = None
depends_on = None


def upgrade():
    # ── target_fanpages: two content modes per fanpage ──
    publishmode = postgresql.ENUM("auto", "manual_review", name="publishmode", create_type=False)
    op.add_column("target_fanpages", sa.Column("mode1_ig_repost_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("target_fanpages", sa.Column("mode2_news_content_enabled", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("target_fanpages", sa.Column("mode2_publish_mode", publishmode, nullable=False, server_default="manual_review"))
    op.add_column("target_fanpages", sa.Column("mode2_gallery_keywords", postgresql.ARRAY(sa.String()), nullable=False, server_default="{}"))
    op.add_column("target_fanpages", sa.Column("mode2_default_template_id", sa.Integer(), nullable=True))  # FK added in Phase 2D with design_templates

    # ── Mode 2 caption criteria (separate set from Mode 1 — decisions log #3) ──
    op.add_column("target_fanpages", sa.Column("mode2_caption_tone", sa.String(64), nullable=False, server_default="informative"))
    op.add_column("target_fanpages", sa.Column("mode2_caption_language", sa.String(8), nullable=False, server_default="en"))
    op.add_column("target_fanpages", sa.Column("mode2_caption_max_length", sa.Integer(), nullable=False, server_default="500"))
    op.add_column("target_fanpages", sa.Column("mode2_caption_hashtag_count", sa.Integer(), nullable=False, server_default="5"))
    op.add_column("target_fanpages", sa.Column("mode2_caption_cta_text", sa.String(256), nullable=False, server_default=""))
    op.add_column("target_fanpages", sa.Column("mode2_caption_custom_prompt", sa.Text(), nullable=False, server_default=""))
    op.add_column("target_fanpages", sa.Column("mode2_title_max_chars", sa.Integer(), nullable=False, server_default="80"))
    op.add_column("target_fanpages", sa.Column("mode2_source_attribution", sa.Boolean(), nullable=False, server_default="true"))

    # ── fanpage ↔ news_source subscription (same pattern as fanpage_sources) ──
    op.create_table(
        "fanpage_news_sources",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("fanpage_id", sa.Integer(), sa.ForeignKey("target_fanpages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("news_source_id", sa.Integer(), sa.ForeignKey("news_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("fanpage_id", "news_source_id", name="uq_fanpage_news_source"),
    )

    # ── publish_jobs: support news_content jobs alongside ig_repost ──
    contenttype = sa.Enum("ig_repost", "news_content", name="contenttype")
    contenttype.create(op.get_bind(), checkfirst=True)

    op.alter_column("publish_jobs", "post_id", nullable=True)  # news jobs have no IG post
    op.add_column("publish_jobs", sa.Column("content_type", contenttype, nullable=False, server_default="ig_repost"))
    op.add_column("publish_jobs", sa.Column("source_article_id", sa.Integer(), sa.ForeignKey("scraped_articles.id", ondelete="CASCADE"), nullable=True))
    op.add_column("publish_jobs", sa.Column("design_title", sa.Text(), nullable=True))
    op.add_column("publish_jobs", sa.Column("design_image_path", sa.String(512), nullable=True))
    op.add_column("publish_jobs", sa.Column("design_image_url", sa.String(512), nullable=True))
    op.add_column("publish_jobs", sa.Column("design_template_id", sa.Integer(), nullable=True))  # FK added in Phase 2D
    op.create_index("ix_publish_jobs_source_article_id", "publish_jobs", ["source_article_id"])
    op.create_unique_constraint("uq_article_fanpage", "publish_jobs", ["source_article_id", "fanpage_id"])

    # news jobs enter the queue waiting for design rendering (Phase 2D)
    op.execute("ALTER TYPE publishjobstatus ADD VALUE IF NOT EXISTS 'pending_design' BEFORE 'pending_publish'")


def downgrade():
    op.drop_constraint("uq_article_fanpage", "publish_jobs", type_="unique")
    op.drop_index("ix_publish_jobs_source_article_id", table_name="publish_jobs")
    op.drop_column("publish_jobs", "design_template_id")
    op.drop_column("publish_jobs", "design_image_url")
    op.drop_column("publish_jobs", "design_image_path")
    op.drop_column("publish_jobs", "design_title")
    op.drop_column("publish_jobs", "source_article_id")
    op.drop_column("publish_jobs", "content_type")
    op.alter_column("publish_jobs", "post_id", nullable=False)
    sa.Enum(name="contenttype").drop(op.get_bind(), checkfirst=True)

    op.drop_table("fanpage_news_sources")

    for col in (
        "mode2_source_attribution", "mode2_title_max_chars", "mode2_caption_custom_prompt",
        "mode2_caption_cta_text", "mode2_caption_hashtag_count", "mode2_caption_max_length",
        "mode2_caption_language", "mode2_caption_tone", "mode2_default_template_id",
        "mode2_gallery_keywords", "mode2_publish_mode", "mode2_news_content_enabled",
        "mode1_ig_repost_enabled",
    ):
        op.drop_column("target_fanpages", col)
    # note: 'pending_design' enum value is left in place — PG cannot drop enum values
