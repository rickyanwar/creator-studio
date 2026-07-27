"""add design_subtitle to publish_jobs (word-highlight template sub-headline)

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-07-25
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_jobs", sa.Column("design_subtitle", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("publish_jobs", "design_subtitle")
