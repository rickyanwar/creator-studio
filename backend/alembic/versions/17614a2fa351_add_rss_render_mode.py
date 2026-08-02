"""add rss render mode + relax NOT NULL on news_sources selector columns

Adds 'rss' to the rendermode enum: category_url is treated as an RSS/Atom
feed URL, and title/content/image/date all come straight from the feed
items instead of per-article CSS-selector scraping. Added for sites whose
article pages sit behind bot protection (DataDome, etc.) that their RSS
feed isn't covered by. Relaxes article_list_selector/title_selector/
content_selector to nullable since RSS-mode sources don't use them.

Revision ID: 17614a2fa351
Revises: 71c3097c67f2
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "17614a2fa351"
down_revision = "71c3097c67f2"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE rendermode ADD VALUE IF NOT EXISTS 'rss'")
    op.alter_column("news_sources", "article_list_selector", nullable=True)
    op.alter_column("news_sources", "title_selector", nullable=True)
    op.alter_column("news_sources", "content_selector", nullable=True)


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op
    # for the enum. Re-tightening the NOT NULL constraints back would fail
    # if any RSS-mode row has NULLs, so this is a no-op too — a genuine
    # rollback would need to backfill/delete rss-mode rows first.
    pass
