"""fanpage_qa_toggles

Revision ID: c80000000045
Revises: c80000000044
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000045"
down_revision = "c80000000044"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.add_column(
            sa.Column("qa_news_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("qa_ig_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("qa_facebook_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch_op.add_column(
            sa.Column("qa_ai_video_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.drop_column("qa_ai_video_enabled")
        batch_op.drop_column("qa_facebook_enabled")
        batch_op.drop_column("qa_ig_enabled")
        batch_op.drop_column("qa_news_enabled")
