"""add design_caption to publish_jobs (name+caption quote template)

Revision ID: c4d5e6f7a8b9
Revises: b3c4d5e6f7a8
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c4d5e6f7a8b9"
down_revision = "b3c4d5e6f7a8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_jobs", sa.Column("design_caption", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("publish_jobs", "design_caption")
