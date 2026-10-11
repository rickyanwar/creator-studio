"""add timezone and target_country to target_fanpages

Revision ID: 234c3c3d6245
Revises: 234c3c3d6244
Create Date: 2026-10-11 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '234c3c3d6245'
down_revision = '234c3c3d6244'
branch_labels = None
depends_on = None


def upgrade():
    # add columns as nullable first
    op.add_column('target_fanpages', sa.Column('timezone', sa.String(length=64), nullable=True))
    op.add_column('target_fanpages', sa.Column('target_country', sa.String(length=2), nullable=True))

    # backfill existing rows
    op.execute("UPDATE target_fanpages SET timezone = 'Asia/Jakarta', target_country = 'ID' WHERE timezone IS NULL")

    # make timezone not null and set default
    op.alter_column('target_fanpages', 'timezone',
               existing_type=sa.String(length=64),
               nullable=False,
               server_default='Europe/London')
    
    # set default for target_country
    op.alter_column('target_fanpages', 'target_country',
               existing_type=sa.String(length=2),
               nullable=True,
               server_default='GB')


def downgrade():
    op.drop_column('target_fanpages', 'target_country')
    op.drop_column('target_fanpages', 'timezone')
