"""drop image_edit columns from ig_sources

Removes the "Auto-edit image (remove watermark)" feature (Gemini/Nano-Banana
image cleaning driven by ig_sources.image_edit_enabled).

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c9d0e1f2a3b4"
down_revision = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("ig_sources", "image_edit_enabled")
    op.drop_column("ig_sources", "image_edit_custom_prompt")


def downgrade():
    op.add_column("ig_sources", sa.Column("image_edit_custom_prompt", sa.Text(), nullable=True))
    op.add_column(
        "ig_sources",
        sa.Column("image_edit_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )
