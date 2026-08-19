"""add prominence_tier/prominence_checked_at to gallery_keywords

Automatic star/regular/minor classification (app.services.keyword_prominence)
so the gallery downloader can check a genuine star daily even far from their
next event (they generate news year-round — transfers, interviews,
controversies) while throttling a minor/backmarker name harder than the
ordinary far-from-event default. Never manually tagged.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
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
