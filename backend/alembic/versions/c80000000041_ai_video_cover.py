"""ai_video_cover

Revision ID: c80000000041
Revises: c80000000040
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000041"
down_revision = "c80000000040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_jobs") as batch_op:
        batch_op.add_column(sa.Column("cover_lines", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("cover_highlight", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("cover_prompt", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("cover_bg_path", sa.String(length=512), nullable=True))

    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.add_column(
            sa.Column("ai_video_cover_enabled", sa.Boolean(), nullable=False, server_default=sa.true())
        )


def downgrade() -> None:
    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.drop_column("ai_video_cover_enabled")

    with op.batch_alter_table("video_jobs") as batch_op:
        batch_op.drop_column("cover_bg_path")
        batch_op.drop_column("cover_prompt")
        batch_op.drop_column("cover_highlight")
        batch_op.drop_column("cover_lines")
