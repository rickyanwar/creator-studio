"""add pinterest_content to contenttype enum (Mode 5)

Revision ID: cc4cb88f501b
Revises: 12ab34cd56ef
Create Date: 2026-08-24
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "cc4cb88f501b"
down_revision = "12ab34cd56ef"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'pinterest_content'")


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op.
    pass
