"""Add preview_image_path to radar_story_decisions

Revision ID: c80000000000
Revises: c00000000000
Create Date: 2026-10-10 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = 'c80000000000'
down_revision = 'c00000000000'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('radar_story_decisions', sa.Column('preview_image_path', sa.String(length=512), nullable=True))
    op.add_column('radar_story_decisions', sa.Column('preview_status', sa.String(length=16), nullable=True))
    op.add_column('radar_story_decisions', sa.Column('preview_error', sa.String(length=300), nullable=True))

def downgrade() -> None:
    op.drop_column('radar_story_decisions', 'preview_error')
    op.drop_column('radar_story_decisions', 'preview_status')
    op.drop_column('radar_story_decisions', 'preview_image_path')
