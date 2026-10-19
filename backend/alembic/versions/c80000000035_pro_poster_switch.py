"""pro_poster_switch

Revision ID: c80000000035
Revises: c80000000034
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000035"
down_revision = "c80000000034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(
            sa.Column("pro_poster_enabled", sa.Boolean(), nullable=False, server_default=sa.text("false"))
        )
        batch_op.add_column(
            sa.Column("pro_poster_fanpage_ids", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("pro_poster_fanpage_ids")
        batch_op.drop_column("pro_poster_enabled")
