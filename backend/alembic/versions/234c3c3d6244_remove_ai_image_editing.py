"""remove_ai_image_editing

Revision ID: 234c3c3d6244
Revises: c1d2e3f4a5b6
Create Date: 2026-10-05 16:06:14.816237

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '234c3c3d6244'
down_revision: Union[str, None] = 'c1d2e3f4a5b6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("UPDATE publish_jobs SET status='pending_caption' WHERE status='pending_watermark'")
    op.execute("UPDATE posts SET status='stored' WHERE status='editing_image'")


def downgrade() -> None:
    pass
