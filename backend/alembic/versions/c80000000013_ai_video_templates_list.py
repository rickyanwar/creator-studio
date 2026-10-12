"""add target_fanpages.ai_video_templates JSON column

Revision ID: c80000000013
Revises: c80000000012
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000013"
down_revision = "c80000000012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("target_fanpages", sa.Column("ai_video_templates", sa.JSON(), nullable=True))
    op.execute(
        "UPDATE target_fanpages "
        "SET ai_video_templates = json_build_array(ai_video_template) "
        "WHERE ai_video_template IS NOT NULL AND ai_video_template != ''"
    )


def downgrade() -> None:
    op.drop_column("target_fanpages", "ai_video_templates")
