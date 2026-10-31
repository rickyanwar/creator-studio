"""Add session_flagged_at to gflow_accounts.

Revision ID: c80000000050
Revises: c80000000049
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000050"
down_revision = "c80000000049"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gflow_accounts", sa.Column("session_flagged_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("gflow_accounts", "session_flagged_at")
