"""widen ai_copy_events.final_provider

Text contexts store "router"/"gemini"/"groq" here, but vision contexts
(design_images.py's _vision_chat, added right after this table) store the
specific 9Router model name (e.g. "ag/gemini-3.5-flash-low") — String(16)
was too narrow and every vision event insert failed silently until this was
widened.

Revision ID: e9f0a1b2c3d4
Revises: d7e8f9a0b1c2
Create Date: 2026-08-16
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e9f0a1b2c3d4"
down_revision = "d7e8f9a0b1c2"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column(
        "ai_copy_events", "final_provider",
        type_=sa.String(length=64),
        existing_type=sa.String(length=16),
    )


def downgrade():
    op.alter_column(
        "ai_copy_events", "final_provider",
        type_=sa.String(length=16),
        existing_type=sa.String(length=64),
    )
