"""add next_event_datetime_utc/event_time_checked_at to gallery_keywords

Precise UTC event start time, when a targeted schedule search finds one (see
app.services.event_calendar.detect_event_time) — refines the bare
next_event_date window so download_all_keywords can tighten checking
specifically around "event time + Getty's ~2-3h upload lag" instead of
blindly polling all day at a flat interval.

Revision ID: 8654b4bc91dc
Revises: 7266d65b50e4
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "8654b4bc91dc"
down_revision = "7266d65b50e4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_keywords", sa.Column("next_event_datetime_utc", sa.DateTime(), nullable=True))
    op.add_column("gallery_keywords", sa.Column("event_time_checked_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("gallery_keywords", "event_time_checked_at")
    op.drop_column("gallery_keywords", "next_event_datetime_utc")
