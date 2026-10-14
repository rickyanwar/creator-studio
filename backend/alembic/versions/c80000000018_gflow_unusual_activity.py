"""gflow unusual-activity pause + per-job counter

Revision ID: c80000000018
Revises: c80000000017
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000018"
down_revision = "c80000000017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gflow_workers", sa.Column("paused_until", sa.DateTime(), nullable=True))
    op.add_column("video_jobs", sa.Column("unusual_hits", sa.Integer(), nullable=False, server_default="0"))


def downgrade() -> None:
    op.drop_column("video_jobs", "unusual_hits")
    op.drop_column("gflow_workers", "paused_until")
