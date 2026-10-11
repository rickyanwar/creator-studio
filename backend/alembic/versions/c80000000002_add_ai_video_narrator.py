"""add per-fanpage AI video narrator

Revision ID: c80000000002
Revises: c80000000001
"""
from alembic import op
import sqlalchemy as sa


revision = "c80000000002"
down_revision = "c80000000001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("ai_video_narrator", sa.String(length=32), nullable=True))


def downgrade() -> None:
    op.drop_column("target_fanpages", "ai_video_narrator")
