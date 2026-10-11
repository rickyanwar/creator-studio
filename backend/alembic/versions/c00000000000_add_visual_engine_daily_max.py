"""add visual engine daily max

Revision ID: c00000000000
Revises: ba44b4096f1c
Create Date: 2026-10-10 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c00000000000'
down_revision = 'ba44b4096f1c'
branch_labels = None
depends_on = None

def upgrade():
    op.add_column('settings', sa.Column('visual_engine_daily_max', sa.Integer(), server_default='60', nullable=False))

def downgrade():
    op.drop_column('settings', 'visual_engine_daily_max')
