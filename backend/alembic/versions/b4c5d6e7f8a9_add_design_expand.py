"""add design_expand toggle (expand-to-fill: reflect-extend / fit+blur)

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("design_expand", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("target_fanpages", "design_expand")
