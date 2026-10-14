"""ai_video_seconds_and_credit_caps

Revision ID: c80000000020
Revises: c80000000019
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000020"
down_revision = "c80000000019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("ai_video_seconds_min", sa.Integer(), nullable=True))
    op.add_column("target_fanpages", sa.Column("ai_video_seconds_max", sa.Integer(), nullable=True))
    op.add_column("target_fanpages", sa.Column("ai_video_max_credits_per_video", sa.Integer(), nullable=True))
    op.add_column("target_fanpages", sa.Column("ai_video_max_credits_per_day", sa.Integer(), nullable=True))
    op.execute(
        "UPDATE target_fanpages SET ai_video_seconds_max = ai_video_clips * 10, "
        "ai_video_seconds_min = COALESCE(ai_video_clips_min, ai_video_clips) * 10"
    )


def downgrade() -> None:
    op.drop_column("target_fanpages", "ai_video_max_credits_per_day")
    op.drop_column("target_fanpages", "ai_video_max_credits_per_video")
    op.drop_column("target_fanpages", "ai_video_seconds_max")
    op.drop_column("target_fanpages", "ai_video_seconds_min")
