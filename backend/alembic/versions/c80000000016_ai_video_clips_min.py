"""add ai_video_clips_min to target_fanpages

Revision ID: c80000000016
Revises: c80000000015
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000016"
down_revision = "c80000000015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("ai_video_clips_min", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("target_fanpages", "ai_video_clips_min")
