"""ai_video_vision_qc_default_on

Revision ID: c80000000024
Revises: c80000000023
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000024"
down_revision = "c80000000023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "settings",
        "ai_video_vision_qc_enabled",
        server_default=sa.text("true"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "settings",
        "ai_video_vision_qc_enabled",
        server_default=sa.text("false"),
        existing_type=sa.Boolean(),
        existing_nullable=False,
    )
