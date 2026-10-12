"""add Mode 7 and Mode 8 video length settings

Revision ID: c80000000011
Revises: c80000000010
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000011"
down_revision = "c80000000010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_fanpages",
        sa.Column("yt_clip_title_max_chars", sa.Integer(), nullable=False, server_default="60"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("yt_clip_description_max_chars", sa.Integer(), nullable=False, server_default="180"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("ai_video_title_max_chars", sa.Integer(), nullable=False, server_default="100"),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("ai_video_description_max_chars", sa.Integer(), nullable=False, server_default="600"),
    )


def downgrade() -> None:
    op.drop_column("target_fanpages", "ai_video_description_max_chars")
    op.drop_column("target_fanpages", "ai_video_title_max_chars")
    op.drop_column("target_fanpages", "yt_clip_description_max_chars")
    op.drop_column("target_fanpages", "yt_clip_title_max_chars")
