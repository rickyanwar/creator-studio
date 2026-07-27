"""add watermark_image_url to target_fanpages (logo watermark on designs)

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e7f8a9b0c1d2"
down_revision = "d6e7f8a9b0c1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("target_fanpages", sa.Column("watermark_image_url", sa.String(512), nullable=True))


def downgrade():
    op.drop_column("target_fanpages", "watermark_image_url")
