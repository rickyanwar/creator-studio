"""raise default publish_daily_limit from 35 to 45

Part of widening publish pacing (anti-bot-detection): the daily cap was
capping some fanpages' real content volume (e.g. one fanpage's steady ~34
posts/day was nearly bumping the 35 cap, so a single burst day created a
backlog that took days to drain even after the scheduler catch-up fix).
Bumps the column default for new fanpages, and any existing fanpage still
sitting at the old default (35) up to the new one (45) — fanpages that were
deliberately configured to something else (e.g. 60) are left untouched.

Revision ID: fd301515b276
Revises: b7f3a9c1d5e2
Create Date: 2026-08-02
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "fd301515b276"
down_revision = "b7f3a9c1d5e2"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE target_fanpages ALTER COLUMN publish_daily_limit SET DEFAULT 45")
    op.execute("UPDATE target_fanpages SET publish_daily_limit = 45 WHERE publish_daily_limit = 35")


def downgrade():
    op.execute("ALTER TABLE target_fanpages ALTER COLUMN publish_daily_limit SET DEFAULT 35")
    op.execute("UPDATE target_fanpages SET publish_daily_limit = 35 WHERE publish_daily_limit = 45")
