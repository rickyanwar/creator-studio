"""add comment fields to burners

Revision ID: 6c7d8e9f0a1b
Revises: 5b6c7d8e9f0a
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "6c7d8e9f0a1b"
down_revision = "5b6c7d8e9f0a"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("burner_accounts", sa.Column("comment_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("burner_accounts", sa.Column("last_comment_at", sa.DateTime(), nullable=True))


def downgrade():
    op.drop_column("burner_accounts", "last_comment_at")
    op.drop_column("burner_accounts", "comment_enabled")
