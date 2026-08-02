"""add gallery_ai_filter_last_criteria to settings

Remembers the last-used criteria text for the manual "Run AI Filter" tool on
the Gallery page (scan_gallery_closeup_filter) so the admin doesn't have to
retype it each time. Purely a UI convenience — never read by any automatic
pipeline.

Revision ID: 71c3097c67f2
Revises: fd301515b276
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "71c3097c67f2"
down_revision = "fd301515b276"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "settings",
        sa.Column("gallery_ai_filter_last_criteria", sa.Text(), nullable=True),
    )


def downgrade():
    op.drop_column("settings", "gallery_ai_filter_last_criteria")
