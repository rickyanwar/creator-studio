"""add last_image_marker to publish_jobs

Tracks which image the design pipeline actually picked last render (e.g.
"gallery:123" or "scraped:https://...") so the History "Re-edit with new
image" action can pass it back into select_image_for_job as something to
skip on the retry — otherwise a re-render can land on the exact same photo
again (deterministic scraped_image_url, or a niche gallery pool with only
one eligible image).

Revision ID: 1b9067dc4dcd
Revises: 4f2ef8d6a40c
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1b9067dc4dcd"
down_revision = "4f2ef8d6a40c"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("publish_jobs", sa.Column("last_image_marker", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("publish_jobs", "last_image_marker")
