"""add facebook_recreate to contenttype enum (Mode 6)

Revision ID: d33238f4b363
Revises: f165c0922101
Create Date: 2026-09-24
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d33238f4b363"
down_revision = "f165c0922101"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'facebook_recreate'")


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op.
    pass
