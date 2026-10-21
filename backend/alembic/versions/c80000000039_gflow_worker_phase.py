"""gflow_worker_phase

Revision ID: c80000000039
Revises: c80000000038
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000039"
down_revision = "c80000000038"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("gflow_workers", sa.Column("phase", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("gflow_workers", "phase")
