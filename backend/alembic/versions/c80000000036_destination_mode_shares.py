"""destination_mode_shares

Revision ID: c80000000036
Revises: c80000000035
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c80000000036"
down_revision = "c80000000035"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "social_destinations",
        sa.Column(
            "mode_shares",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "social_destinations",
        sa.Column(
            "borrow_hours_before_sleep",
            sa.Integer(),
            nullable=False,
            server_default="3",
        ),
    )
    op.add_column(
        "social_destinations",
        sa.Column(
            "news_stale_hours",
            sa.Integer(),
            nullable=False,
            server_default="24",
        ),
    )


def downgrade() -> None:
    op.drop_column("social_destinations", "news_stale_hours")
    op.drop_column("social_destinations", "borrow_hours_before_sleep")
    op.drop_column("social_destinations", "mode_shares")
