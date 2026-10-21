"""vision_check_cache

Revision ID: c80000000040
Revises: c80000000039
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000040"
down_revision = "c80000000039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vision_check_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("image_sha256", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=512), nullable=False),
        sa.Column("result", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_vision_check_cache_kind_image_key",
        "vision_check_cache",
        ["kind", "image_sha256", "key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_vision_check_cache_kind_image_key", table_name="vision_check_cache")
    op.drop_table("vision_check_cache")
