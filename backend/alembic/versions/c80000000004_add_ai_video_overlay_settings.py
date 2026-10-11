"""add ai_video overlay settings to target_fanpages

Revision ID: c80000000004
Revises: c80000000003
"""
from alembic import op
import sqlalchemy as sa


revision = "c80000000004"
down_revision = "c80000000003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("ai_video_logo_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("target_fanpages", sa.Column("ai_video_logo_size_pct", sa.SmallInteger(), nullable=False, server_default="14"))
    op.add_column("target_fanpages", sa.Column("ai_video_logo_position", sa.String(length=16), nullable=False, server_default="top_right"))
    op.add_column("target_fanpages", sa.Column("ai_video_logo_margin_pct", sa.SmallInteger(), nullable=False, server_default="3"))
    op.add_column("target_fanpages", sa.Column("ai_video_captions_enabled", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("target_fanpages", sa.Column("ai_video_caption_size", sa.SmallInteger(), nullable=False, server_default="76"))
    op.add_column("target_fanpages", sa.Column("ai_video_caption_bottom_pct", sa.SmallInteger(), nullable=False, server_default="29"))
    op.add_column("target_fanpages", sa.Column("ai_video_caption_highlight", sa.String(length=7), nullable=False, server_default="#FFE500"))


def downgrade() -> None:
    op.drop_column("target_fanpages", "ai_video_caption_highlight")
    op.drop_column("target_fanpages", "ai_video_caption_bottom_pct")
    op.drop_column("target_fanpages", "ai_video_caption_size")
    op.drop_column("target_fanpages", "ai_video_captions_enabled")
    op.drop_column("target_fanpages", "ai_video_logo_margin_pct")
    op.drop_column("target_fanpages", "ai_video_logo_position")
    op.drop_column("target_fanpages", "ai_video_logo_size_pct")
    op.drop_column("target_fanpages", "ai_video_logo_enabled")
