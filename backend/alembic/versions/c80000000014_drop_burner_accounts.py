"""drop burner_accounts table and related columns

Revision ID: c80000000014
Revises: c80000000013
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "c80000000014"
down_revision = "c80000000013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Drop foreign key constraint and column on ig_sources
    op.drop_constraint("ig_sources_burner_account_id_fkey", "ig_sources", type_="foreignkey")
    op.drop_column("ig_sources", "burner_account_id")

    # 2. Drop burner_accounts table
    op.drop_index("ix_burner_accounts_ig_username", table_name="burner_accounts")
    op.drop_table("burner_accounts")

    # 3. Drop burnerstatus enum type
    burnerstatus = postgresql.ENUM("active", "challenged", "rate_limited", "banned", name="burnerstatus")
    burnerstatus.drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    # 1. Recreate burner_accounts table (creates burnerstatus enum automatically if not exist)
    burner_status_enum = sa.Enum("active", "challenged", "rate_limited", "banned", name="burnerstatus")
    op.create_table(
        "burner_accounts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ig_username", sa.String(64), unique=True, nullable=False),
        sa.Column("encrypted_password", sa.String(512), nullable=False),
        sa.Column("encrypted_session", sa.String(4096), nullable=True),
        sa.Column("proxy_url", sa.String(256), nullable=True),
        sa.Column(
            "status",
            burner_status_enum,
            nullable=False,
            server_default="active",
        ),
        sa.Column("requests_today", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("cooldown_until", sa.DateTime(), nullable=True),
        sa.Column("last_error", sa.String(512), nullable=True),
        sa.Column("story_enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("last_story_at", sa.DateTime(), nullable=True),
        sa.Column("comment_enabled", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("last_comment_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_burner_accounts_ig_username", "burner_accounts", ["ig_username"])

    # 2. Recreate ig_sources.burner_account_id column and foreign key
    op.add_column("ig_sources", sa.Column("burner_account_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "ig_sources_burner_account_id_fkey",
        "ig_sources",
        "burner_accounts",
        ["burner_account_id"],
        ["id"],
    )
