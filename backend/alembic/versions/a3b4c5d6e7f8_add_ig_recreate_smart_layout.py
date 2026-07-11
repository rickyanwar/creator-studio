"""add ig_recreate smart-layout toggle + split template

Revision ID: a3b4c5d6e7f8
Revises: f2a3b4c5d6e7
Create Date: 2026-07-11
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a3b4c5d6e7f8"
down_revision = "f2a3b4c5d6e7"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("ig_recreate_smart_layout", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("target_fanpages", sa.Column("ig_recreate_split_template_id", sa.Integer(), nullable=True))


def downgrade():
    op.drop_column("target_fanpages", "ig_recreate_split_template_id")
    op.drop_column("target_fanpages", "ig_recreate_smart_layout")
