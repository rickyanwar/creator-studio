"""add niche/category to gallery_keywords (e.g. F1, MotoGP, UFC — for
filtering and bulk organization; free text, not a fixed enum)

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("gallery_keywords", sa.Column("niche", sa.String(64), nullable=True))
    op.create_index("ix_gallery_keywords_niche", "gallery_keywords", ["niche"])


def downgrade():
    op.drop_index("ix_gallery_keywords_niche", table_name="gallery_keywords")
    op.drop_column("gallery_keywords", "niche")
