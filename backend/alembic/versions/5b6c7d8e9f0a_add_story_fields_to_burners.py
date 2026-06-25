"""add story fields to burner_accounts

Revision ID: 5b6c7d8e9f0a
Revises: 4a1b2c3d4e5f
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "5b6c7d8e9f0a"
down_revision = "4a1b2c3d4e5f"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("burner_accounts", sa.Column("story_enabled", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("burner_accounts", sa.Column("last_story_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("burner_accounts", "last_story_at")
    op.drop_column("burner_accounts", "story_enabled")
