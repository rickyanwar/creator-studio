"""gflow_account_strikes

Revision ID: c80000000042
Revises: c80000000041
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000042"
down_revision = "c80000000041"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gflow_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("unusual_strikes", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("last_unusual_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("gflow_accounts") as batch_op:
        batch_op.drop_column("last_unusual_at")
        batch_op.drop_column("unusual_strikes")
