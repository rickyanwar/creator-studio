"""add captured_at to gallery_images

Getty editorial captions dateline the shot ("...on August 09, 2026 in
Northampton, England.") — image_downloader._parse_caption_date reads this
straight out of the markdown page already fetched for the search results, at
zero extra web/fetch cost. Lets photo-selection logic later prefer the
freshest-dated photo instead of relying only on search-result ordering.

Revision ID: 0a6f595cb9d0
Revises: 99b7247ea4ee
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "0a6f595cb9d0"
down_revision = "99b7247ea4ee"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_images", sa.Column("captured_at", sa.Date(), nullable=True))
    op.create_index("ix_gallery_images_captured_at", "gallery_images", ["captured_at"])


def downgrade():
    op.drop_index("ix_gallery_images_captured_at", table_name="gallery_images")
    op.drop_column("gallery_images", "captured_at")
