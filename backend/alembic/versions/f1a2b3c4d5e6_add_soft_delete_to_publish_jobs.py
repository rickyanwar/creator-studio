"""add is_deleted/deleted_at to publish_jobs (soft delete from History — the
row is kept so the unique post_id/fanpage_id and source_article_id/fanpage_id
constraints still prevent the same content being reposted)

Revision ID: f1a2b3c4d5e6
Revises: e8f9a0b1c2d3
Create Date: 2026-07-27
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "f1a2b3c4d5e6"
down_revision = "e8f9a0b1c2d3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "publish_jobs",
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column("publish_jobs", sa.Column("deleted_at", sa.DateTime(), nullable=True))
    op.create_index("ix_publish_jobs_is_deleted", "publish_jobs", ["is_deleted"])


def downgrade():
    op.drop_index("ix_publish_jobs_is_deleted", table_name="publish_jobs")
    op.drop_column("publish_jobs", "deleted_at")
    op.drop_column("publish_jobs", "is_deleted")
