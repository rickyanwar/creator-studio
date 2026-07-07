"""add news scraper tables (news_sources, scraped_articles)

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa

revision = "e4f5a6b7c8d9"
down_revision = "d3e4f5a6b7c8"
branch_labels = None
depends_on = None


def upgrade():
    rendermode = sa.Enum("static", "js", name="rendermode")
    articlestatus = sa.Enum("scraped", "copywritten", "designed", "published", "skipped", name="articlestatus")

    op.create_table(
        "news_sources",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category_url", sa.String(512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("scrape_interval_minutes", sa.Integer(), nullable=False, server_default="60"),
        sa.Column("render_mode", rendermode, nullable=False, server_default="static"),
        sa.Column("article_list_selector", sa.String(256), nullable=False),
        sa.Column("article_link_attribute", sa.String(64), nullable=False, server_default="href"),
        sa.Column("title_selector", sa.String(256), nullable=False),
        sa.Column("content_selector", sa.String(256), nullable=False),
        sa.Column("image_selector", sa.String(256), nullable=True),
        sa.Column("date_selector", sa.String(256), nullable=True),
        sa.Column("last_scraped_at", sa.DateTime(), nullable=True),
        sa.Column("last_scrape_error", sa.String(512), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "scraped_articles",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("news_source_id", sa.Integer(), sa.ForeignKey("news_sources.id", ondelete="CASCADE"), nullable=False),
        sa.Column("article_url", sa.String(1024), nullable=False),
        sa.Column("scraped_title", sa.Text(), nullable=False),
        sa.Column("scraped_content", sa.Text(), nullable=False),
        sa.Column("scraped_image_url", sa.String(1024), nullable=True),
        sa.Column("is_processed", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("status", articlestatus, nullable=False, server_default="scraped"),
        sa.Column("scraped_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scraped_articles_news_source_id", "scraped_articles", ["news_source_id"])
    op.create_unique_constraint("uq_scraped_articles_article_url", "scraped_articles", ["article_url"])
    op.create_index("ix_scraped_articles_status", "scraped_articles", ["status"])


def downgrade():
    op.drop_table("scraped_articles")
    op.drop_table("news_sources")
    sa.Enum(name="articlestatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="rendermode").drop(op.get_bind(), checkfirst=True)
