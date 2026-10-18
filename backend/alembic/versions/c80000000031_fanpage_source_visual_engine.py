"""fanpage_source_visual_engine

Revision ID: c80000000031
Revises: c80000000030
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000031"
down_revision = "c80000000030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "fanpage_sources",
        sa.Column("visual_engine_override", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("fanpage_sources", "visual_engine_override")
