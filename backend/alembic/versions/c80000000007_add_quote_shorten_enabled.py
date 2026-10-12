"""add quote_shorten_enabled to target_fanpages

Revision ID: c80000000007
Revises: c80000000005
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000007"
down_revision = "c80000000005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_fanpages",
        sa.Column("quote_shorten_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("target_fanpages", "quote_shorten_enabled")
