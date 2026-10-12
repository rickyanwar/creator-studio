"""unify fanpage language

Revision ID: c80000000010
Revises: c80000000009
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000010"
down_revision = "c80000000009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Safety sync: set mode2_caption_language = caption_language on every row.
    # Keep both columns; no drops.
    op.execute(
        "UPDATE target_fanpages SET mode2_caption_language = caption_language "
        "WHERE caption_language IS NOT NULL"
    )


def downgrade() -> None:
    pass
