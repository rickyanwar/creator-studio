"""fanpage_hour_weights

Revision ID: c80000000032
Revises: c80000000031
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c80000000032"
down_revision = "c80000000031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "target_fanpages",
        sa.Column(
            "publish_hour_weights",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("target_fanpages", "publish_hour_weights")
