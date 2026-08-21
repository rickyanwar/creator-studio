"""add is_breaking to publish_jobs

Lets news_copywriter's classification call flag a job as significant
breaking news, so publisher._next_schedule_at can skip the normal
per-fanpage pacing/daily-cap queue and post it near-immediately instead of
wherever the queue currently lands (see news_copywriter.build_news_copy_prompt
TASK 1's new is_breaking field).

Revision ID: 11fb01729ede
Revises: e10eb322bf3e
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "11fb01729ede"
down_revision = "e10eb322bf3e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "publish_jobs",
        sa.Column("is_breaking", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("publish_jobs", "is_breaking")
