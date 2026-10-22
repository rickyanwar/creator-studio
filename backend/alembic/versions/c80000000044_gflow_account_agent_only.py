"""gflow_account_agent_only

Revision ID: c80000000044
Revises: c80000000043
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000044"
down_revision = "c80000000043"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("gflow_accounts") as batch_op:
        batch_op.add_column(
            sa.Column("agent_only", sa.Boolean(), nullable=False, server_default=sa.false())
        )


def downgrade() -> None:
    with op.batch_alter_table("gflow_accounts") as batch_op:
        batch_op.drop_column("agent_only")
