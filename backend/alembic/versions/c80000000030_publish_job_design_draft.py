"""publish_job_design_draft

Revision ID: c80000000030
Revises: c80000000029
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c80000000030"
down_revision = "c80000000029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "publish_jobs",
        sa.Column(
            "design_draft_json",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=True,
        ),
    )
    op.add_column(
        "publish_jobs",
        sa.Column("design_draft_saved_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("publish_jobs", "design_draft_saved_at")
    op.drop_column("publish_jobs", "design_draft_json")
