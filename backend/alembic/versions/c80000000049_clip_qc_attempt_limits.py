"""Configurable clip QC attempts with a per-job snapshot.

Revision ID: c80000000049
Revises: c80000000048
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000049"
down_revision = "c80000000048"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_fanpages",
        sa.Column("ai_video_clip_qc_max_attempts", sa.Integer(), nullable=False, server_default="4"),
    )
    op.add_column("video_jobs", sa.Column("clip_qc_max_attempts", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_jobs", "clip_qc_max_attempts")
    op.drop_column("target_fanpages", "ai_video_clip_qc_max_attempts")
