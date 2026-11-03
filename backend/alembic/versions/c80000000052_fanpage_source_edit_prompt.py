"""Add per-source ChatGPT edit prompts.

Revision ID: c80000000052
Revises: c80000000051
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000052"
down_revision = "c80000000051"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fanpage_sources", sa.Column("chatgpt_edit_prompt", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("fanpage_sources", "chatgpt_edit_prompt")
