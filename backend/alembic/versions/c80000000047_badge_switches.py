"""badge_switches

Revision ID: c80000000047
Revises: c80000000046
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000047"
down_revision = "c80000000046"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.add_column(
            sa.Column("badge_breaking_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("badge_discussion_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("news_badge_percent", sa.Integer(), nullable=False, server_default="100")
        )


def downgrade() -> None:
    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.drop_column("news_badge_percent")
        batch_op.drop_column("badge_discussion_enabled")
        batch_op.drop_column("badge_breaking_enabled")
