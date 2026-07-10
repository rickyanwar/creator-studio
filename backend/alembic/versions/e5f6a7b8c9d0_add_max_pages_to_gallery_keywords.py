"""add max_pages to gallery_keywords

Per-keyword count of search-result pages to fetch each run (default 10).

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e5f6a7b8c9d0"
down_revision = "d4e5f6a7b8c9"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "gallery_keywords",
        sa.Column("max_pages", sa.Integer(), nullable=False, server_default="10"),
    )


def downgrade():
    op.drop_column("gallery_keywords", "max_pages")
