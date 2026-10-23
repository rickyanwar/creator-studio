"""argon_prompt_polish

Revision ID: c80000000046
Revises: c80000000045
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000046"
down_revision = "c80000000045"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("video_ideas") as batch_op:
        batch_op.add_column(
            sa.Column("prepared", sa.JSON(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("prepared_at", sa.DateTime(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("polish_attempts", sa.Integer(), nullable=False, server_default="0")
        )
        batch_op.add_column(
            sa.Column("polish_error", sa.Text(), nullable=True)
        )

    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.add_column(
            sa.Column("ai_video_prompt_polish_enabled", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("settings") as batch_op:
        batch_op.add_column(
            sa.Column("prompt_polish_base_url", sa.String(256), nullable=False, server_default="http://172.17.0.1:8098")
        )
        batch_op.add_column(
            sa.Column("prompt_polish_token_encrypted", sa.String(512), nullable=True)
        )
        batch_op.add_column(
            sa.Column("prompt_polish_model", sa.String(64), nullable=False, server_default="gemini-argon")
        )


def downgrade() -> None:
    with op.batch_alter_table("settings") as batch_op:
        batch_op.drop_column("prompt_polish_model")
        batch_op.drop_column("prompt_polish_token_encrypted")
        batch_op.drop_column("prompt_polish_base_url")

    with op.batch_alter_table("target_fanpages") as batch_op:
        batch_op.drop_column("ai_video_prompt_polish_enabled")

    with op.batch_alter_table("video_ideas") as batch_op:
        batch_op.drop_column("polish_error")
        batch_op.drop_column("polish_attempts")
        batch_op.drop_column("prepared_at")
        batch_op.drop_column("prepared")
