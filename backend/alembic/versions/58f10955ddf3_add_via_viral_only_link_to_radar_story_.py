"""Add via_viral_only_link to radar_story_decisions

Revision ID: 58f10955ddf3
Revises: b1c2d3e4f5a7
Create Date: 2026-10-10 02:39:12.024027

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '58f10955ddf3'
down_revision: Union[str, None] = 'b1c2d3e4f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("radar_story_decisions", sa.Column("via_viral_only_link", sa.Boolean(), server_default="false", nullable=False))

def downgrade() -> None:
    op.drop_column("radar_story_decisions", "via_viral_only_link")
