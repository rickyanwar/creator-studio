"""add pinterest_custom_prompt + pinterest_hashtag_count to target_fanpages (Mode 5)

Revision ID: 97cd344f1a3f
Revises: 3136ab873a6d
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "97cd344f1a3f"
down_revision = "3136ab873a6d"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_custom_prompt", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_hashtag_count", sa.Integer(), nullable=False, server_default="5"),
    )


def downgrade():
    op.drop_column("target_fanpages", "pinterest_hashtag_count")
    op.drop_column("target_fanpages", "pinterest_custom_prompt")
