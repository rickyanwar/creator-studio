"""add publish sleep window + daily limit to target_fanpages

Per-fanpage publish pacing so auto-publish behaves less bot-like: a WIB
sleep window (no real page admin schedules posts at 3am) and a daily
publish cap (a page that never stops posting is itself a signal,
independent of the gap between posts). Existing fanpages are backfilled
with a sensible default sleep window (00:00-06:00 WIB) — nullable, so it
can be disabled per fanpage by setting both to null.

Revision ID: 4f2ef8d6a40c
Revises: 336427a50c77
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "4f2ef8d6a40c"
down_revision = "336427a50c77"
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()

    op.add_column("target_fanpages", sa.Column("publish_sleep_start_hour", sa.Integer(), nullable=True))
    op.add_column("target_fanpages", sa.Column("publish_sleep_end_hour", sa.Integer(), nullable=True))
    op.add_column(
        "target_fanpages",
        sa.Column("publish_daily_limit", sa.Integer(), nullable=False, server_default="35"),
    )

    conn.execute(sa.text(
        "UPDATE target_fanpages SET publish_sleep_start_hour = 0, publish_sleep_end_hour = 6"
    ))


def downgrade():
    op.drop_column("target_fanpages", "publish_daily_limit")
    op.drop_column("target_fanpages", "publish_sleep_end_hour")
    op.drop_column("target_fanpages", "publish_sleep_start_hour")
