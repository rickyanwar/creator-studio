"""add prominence_tier/prominence_checked_at to gallery_keywords

Automatic star/regular/minor classification (app.services.keyword_prominence)
so the gallery downloader can check a genuine star daily even far from their
next event (they generate news year-round — transfers, interviews,
controversies) while throttling a minor/backmarker name harder than the
ordinary far-from-event default. Never manually tagged.

Revision ID: 7266d65b50e4
Revises: 0a6f595cb9d0
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "7266d65b50e4"
down_revision = "0a6f595cb9d0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_keywords", sa.Column("prominence_tier", sa.String(16), nullable=True))
    op.add_column("gallery_keywords", sa.Column("prominence_checked_at", sa.DateTime(), nullable=True))
    op.create_index("ix_gallery_keywords_prominence_tier", "gallery_keywords", ["prominence_tier"])


def downgrade():
    op.drop_index("ix_gallery_keywords_prominence_tier", table_name="gallery_keywords")
    op.drop_column("gallery_keywords", "prominence_checked_at")
    op.drop_column("gallery_keywords", "prominence_tier")
