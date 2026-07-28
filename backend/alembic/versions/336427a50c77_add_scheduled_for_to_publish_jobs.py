"""add scheduled_for to publish_jobs

Tracks the actual Facebook go-live time we sent Repliz as scheduleAt —
distinct from published_at (when the API call happened). Used to space
out consecutive posts on the same fanpage: unlike published_at, this
reflects the real future slot even when it was pushed out by an earlier
job's spacing requirement.

Revision ID: 336427a50c77
Revises: 7d4dc65cb529
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "336427a50c77"
down_revision = "7d4dc65cb529"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_jobs", sa.Column("scheduled_for", sa.DateTime(), nullable=True))
    op.create_index("ix_publish_jobs_scheduled_for", "publish_jobs", ["scheduled_for"])


def downgrade():
    op.drop_index("ix_publish_jobs_scheduled_for", table_name="publish_jobs")
    op.drop_column("publish_jobs", "scheduled_for")
