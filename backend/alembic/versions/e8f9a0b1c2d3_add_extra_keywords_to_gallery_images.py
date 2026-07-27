"""add extra_keywords to gallery_images (a photo can feature more than one
person — lets it match under other names too, beyond the primary keyword)

Revision ID: e8f9a0b1c2d3
Revises: c3d4e5f6a7b8
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "e8f9a0b1c2d3"
down_revision = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "gallery_images",
        sa.Column(
            "extra_keywords",
            postgresql.ARRAY(sa.String(128)),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade():
    op.drop_column("gallery_images", "extra_keywords")
