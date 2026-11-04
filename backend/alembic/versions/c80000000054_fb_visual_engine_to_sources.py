"""Preserve legacy Facebook AI designs as explicit per-source formats.

Revision ID: c80000000054
Revises: c80000000053
"""

from alembic import op

revision = "c80000000054"
down_revision = "c80000000053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "UPDATE facebook_photo_sources SET format = 'chatgpt' WHERE format IS NULL AND "
        "fanpage_id IN (SELECT id FROM target_fanpages WHERE fb_visual_engine = 'chatgpt')"
    )


def downgrade() -> None:
    # Keep explicit source choices and the legacy column intact for safe image rollbacks.
    pass
