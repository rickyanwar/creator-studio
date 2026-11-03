"""Add Mode 8 TTS narration settings and job payload.

Revision ID: c80000000051
Revises: c80000000050
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000051"
down_revision = "c80000000050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("ai_video_narration", sa.JSON(), nullable=True))
    op.add_column("video_jobs", sa.Column("narration", sa.JSON(), nullable=True))
    op.add_column("video_clip_qc", sa.Column("speech", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("video_clip_qc", "speech")
    op.drop_column("video_jobs", "narration")
    op.drop_column("target_fanpages", "ai_video_narration")
