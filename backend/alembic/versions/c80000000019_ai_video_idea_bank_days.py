"""add ai_video_idea_bank_days to settings

Revision ID: c80000000019
Revises: c80000000018
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000019"
down_revision = "c80000000018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("settings", sa.Column("ai_video_idea_bank_days", sa.Integer(), nullable=False, server_default="3"))


def downgrade() -> None:
    op.drop_column("settings", "ai_video_idea_bank_days")
