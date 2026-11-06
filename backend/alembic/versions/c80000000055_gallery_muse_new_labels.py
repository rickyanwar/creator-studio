"""Defer new gallery image gates to Muse when configured."""

from alembic import op
import sqlalchemy as sa

revision = "c80000000055"
down_revision = "c80000000054"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_images", sa.Column("label_pending", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("settings", sa.Column("gallery_new_labeler", sa.String(16), nullable=False, server_default="app"))
    op.add_column("settings", sa.Column("gallery_muse_fallback_hours", sa.Integer(), nullable=False, server_default="6"))
    op.create_index("ix_gallery_images_label_pending", "gallery_images", ["downloaded_at"], postgresql_where=sa.text("label_pending"), if_not_exists=True)


def downgrade():
    op.drop_index("ix_gallery_images_label_pending", table_name="gallery_images")
    op.drop_column("settings", "gallery_muse_fallback_hours")
    op.drop_column("settings", "gallery_new_labeler")
    op.drop_column("gallery_images", "label_pending")
