"""delivery_snapshots

Revision ID: c80000000022
Revises: c80000000021
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000022"
down_revision = "c80000000021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("publish_deliveries", sa.Column("caption_snapshot", sa.Text(), nullable=True))
    op.add_column("publish_deliveries", sa.Column("media_snapshot", sa.JSON(), nullable=True))
    op.add_column("publish_deliveries", sa.Column("media_kind", sa.String(length=16), nullable=True))
    op.add_column("publish_deliveries", sa.Column("title_snapshot", sa.Text(), nullable=True))
    op.add_column("publish_deliveries", sa.Column("alt_snapshot", sa.Text(), nullable=True))
    op.add_column("publish_deliveries", sa.Column("content_type_snapshot", sa.String(length=32), nullable=True))
    op.create_check_constraint(
        "ck_publish_deliveries_media_kind",
        "publish_deliveries",
        "media_kind IS NULL OR media_kind IN ('image', 'album', 'video')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_publish_deliveries_media_kind", "publish_deliveries", type_="check")
    op.drop_column("publish_deliveries", "content_type_snapshot")
    op.drop_column("publish_deliveries", "alt_snapshot")
    op.drop_column("publish_deliveries", "title_snapshot")
    op.drop_column("publish_deliveries", "media_kind")
    op.drop_column("publish_deliveries", "media_snapshot")
    op.drop_column("publish_deliveries", "caption_snapshot")
