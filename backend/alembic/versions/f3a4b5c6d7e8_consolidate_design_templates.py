"""add DesignTemplate.category + TargetFanpage default_quote/news_template_id

Consolidates 3 fanpage template fields (mode2_default_template_id,
ig_recreate_quote_template_id, ig_recreate_news_template_id — set in 2
different UI sections) into 2 category-based fields shared across both the
news-scrape and ig_recreate pipelines: default_quote_template_id and
default_news_template_id. Tags the known shared seed templates with their
category and backfills each fanpage's new fields from its old ones.

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None

_QUOTE_TEMPLATE_NAMES = [
    "Quote Card (word-highlight)",
    "Quote Card (name badge)",
    "Quote Card (word-highlight, name badge)",
]
_NEWS_TEMPLATE_NAMES = [
    "MotoGP News (two-tone headline)",
    "News Highlight — Green · Center (Quote overlay)",
    "News Highlight — Red · Left",
    "News Highlight — Red · Center",
    "News Highlight — Yellow · Left",
    "News Highlight — Green · Left",
    "News Highlight — Yellow · Center",
    "News Highlight — Green · Center",
]


def upgrade():
    conn = op.get_bind()

    op.add_column("design_templates", sa.Column("category", sa.String(16), nullable=True))
    conn.execute(
        sa.text("UPDATE design_templates SET category = 'quote' WHERE name = ANY(:names) AND fanpage_id IS NULL"),
        {"names": _QUOTE_TEMPLATE_NAMES},
    )
    conn.execute(
        sa.text("UPDATE design_templates SET category = 'news' WHERE name = ANY(:names) AND fanpage_id IS NULL"),
        {"names": _NEWS_TEMPLATE_NAMES},
    )

    op.add_column("target_fanpages", sa.Column("default_quote_template_id", sa.Integer(), nullable=True))
    op.add_column("target_fanpages", sa.Column("default_news_template_id", sa.Integer(), nullable=True))
    conn.execute(sa.text("""
        UPDATE target_fanpages
        SET default_quote_template_id = ig_recreate_quote_template_id,
            default_news_template_id = COALESCE(mode2_default_template_id, ig_recreate_news_template_id)
    """))


def downgrade():
    op.drop_column("target_fanpages", "default_news_template_id")
    op.drop_column("target_fanpages", "default_quote_template_id")
    op.drop_column("design_templates", "category")
