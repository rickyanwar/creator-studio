"""widen picture_url to text

Revision ID: 3f50c924b58d
Revises: 001
Create Date: 2026-06-25
"""
from alembic import op
import sqlalchemy as sa

revision = "3f50c924b58d"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "target_fanpages",
        "picture_url",
        existing_type=sa.VARCHAR(length=512),
        type_=sa.Text(),
        existing_nullable=True,
    )


def downgrade():
    op.alter_column(
        "target_fanpages",
        "picture_url",
        existing_type=sa.Text(),
        type_=sa.VARCHAR(length=512),
        existing_nullable=True,
    )
