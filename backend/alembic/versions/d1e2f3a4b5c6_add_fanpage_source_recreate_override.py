"""add per-source ig_recreate_enabled override to fanpage_sources

Revision ID: d1e2f3a4b5c6
Revises: c0d1e2f3a4b5
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d1e2f3a4b5c6"
down_revision = "c0d1e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("fanpage_sources", sa.Column("ig_recreate_enabled", sa.Boolean(), nullable=True))


def downgrade():
    op.drop_column("fanpage_sources", "ig_recreate_enabled")
