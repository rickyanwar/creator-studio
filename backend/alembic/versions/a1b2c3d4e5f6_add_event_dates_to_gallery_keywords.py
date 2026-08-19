"""add next_event_date/event_date_checked_at to gallery_keywords

Part of the event-aware gallery throttle (2026-08-19): download_all_keywords
now spends freely during a keyword's press/practice/race window and
throttles harder outside it, instead of a flat daily interval regardless of
whether anything is imminent. next_event_date is detected automatically by
app.services.event_calendar (mined from scraped articles, falling back to a
paid web-search only when that turns up nothing) — never entered manually.

Revision ID: a1b2c3d4e5f6
Revises: 507e8c06f625
Create Date: 2026-08-19
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "a1b2c3d4e5f6"
down_revision = "507e8c06f625"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_keywords", sa.Column("next_event_date", sa.Date(), nullable=True))
    op.add_column("gallery_keywords", sa.Column("event_date_checked_at", sa.DateTime(), nullable=True))
    op.create_index(
        "ix_gallery_keywords_next_event_date", "gallery_keywords", ["next_event_date"]
    )


def downgrade():
    op.drop_index("ix_gallery_keywords_next_event_date", table_name="gallery_keywords")
    op.drop_column("gallery_keywords", "event_date_checked_at")
    op.drop_column("gallery_keywords", "next_event_date")
