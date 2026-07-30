"""add mode2_editorial_gate_enabled to target_fanpages

Opt-in per-fanpage toggle: before an article is copywritten for a fanpage,
9Router web-search-fact-checks it and judges post-worthiness/engagement — a
rejected article gets no PublishJob for that fanpage. See
app/services/editorial_gate.py and news_copywriter.copywrite_article.

Revision ID: 2c8f4a1e6b3d
Revises: 1b9067dc4dcd
Create Date: 2026-07-30
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2c8f4a1e6b3d"
down_revision = "1b9067dc4dcd"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("mode2_editorial_gate_enabled", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade():
    op.drop_column("target_fanpages", "mode2_editorial_gate_enabled")
