"""gflow_account_cooldown

Revision ID: c80000000038
Revises: c80000000037
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000038"
down_revision = "c80000000037"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gflow_accounts", sa.Column("cooldown_until", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("gflow_accounts", "cooldown_until")
