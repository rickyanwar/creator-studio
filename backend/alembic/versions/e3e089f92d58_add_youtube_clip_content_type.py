"""add youtube_clip to contenttype enum (Mode 7)

Kept in its own migration (same as Mode 6's d33238f4b363): a value added by
ALTER TYPE ... ADD VALUE can't be used in the same transaction, so nothing
else goes in this revision.

Revision ID: e3e089f92d58
Revises: 60fffb49b7b8
Create Date: 2026-09-25
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e3e089f92d58"
down_revision = "60fffb49b7b8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TYPE contenttype ADD VALUE IF NOT EXISTS 'youtube_clip'")


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op.
    pass
