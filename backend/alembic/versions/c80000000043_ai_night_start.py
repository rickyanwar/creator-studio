"""ai_night_start

Revision ID: c80000000043
Revises: c80000000042
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000043"
down_revision = "c80000000042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "settings",
        sa.Column("ai_night_start", sa.String(length=5), nullable=False, server_default="00:00"),
    )


def downgrade() -> None:
    op.drop_column("settings", "ai_night_start")
