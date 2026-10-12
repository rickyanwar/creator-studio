"""add target_fanpages.accent_color

Revision ID: c80000000008
Revises: c80000000006
Create Date: 2026-10-12
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c80000000008"
down_revision = "c80000000006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("accent_color", sa.String(length=9), nullable=True),
    )


def downgrade():
    op.drop_column("target_fanpages", "accent_color")
