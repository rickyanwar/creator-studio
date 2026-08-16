"""add discussion to contenttype enum (Mode 4)

Revision ID: a1c2e3d4f5b6
Revises: 17614a2fa351
Create Date: 2026-08-09
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a1c2e3d4f5b6"
down_revision = "17614a2fa351"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'discussion'")


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op.
    pass
