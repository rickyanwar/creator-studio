"""add design_analysis_json to publish_jobs

Revision ID: c80000000001
Revises: c80000000000
Create Date: 2026-10-11 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'c80000000001'
down_revision = 'c80000000000'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('publish_jobs', sa.Column('design_analysis_json', postgresql.JSONB(astext_type=sa.Text()), nullable=True))

def downgrade() -> None:
    op.drop_column('publish_jobs', 'design_analysis_json')
