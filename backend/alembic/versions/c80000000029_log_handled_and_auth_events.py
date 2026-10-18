"""log_handled_and_auth_events

Revision ID: c80000000029
Revises: c80000000028
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000029"
down_revision = "c80000000028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "log_handled",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_kind", sa.String(length=32), nullable=False),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("handled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("entry_kind", "entry_id", name="uq_log_handled_kind_id"),
    )
    op.create_index("ix_log_handled_id", "log_handled", ["id"])
    op.create_index("ix_log_handled_entry_kind", "log_handled", ["entry_kind"])
    op.create_index("ix_log_handled_entry_id", "log_handled", ["entry_id"])

    op.create_table(
        "auth_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("username", sa.String(length=128), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=256), nullable=True),
    )
    op.create_index("ix_auth_events_id", "auth_events", ["id"])
    op.create_index("ix_auth_events_created_at", "auth_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("auth_events")
    op.drop_table("log_handled")
