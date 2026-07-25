"""add per-source caption criteria (IG + news sources, global overrides)

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-07-12
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None

_COLUMNS = [
    ("caption_tone", sa.String(64)),
    ("caption_language", sa.String(8)),
    ("caption_max_length", sa.Integer()),
    ("caption_hashtag_count", sa.Integer()),
    ("caption_cta_text", sa.String(256)),
    ("caption_custom_prompt", sa.Text()),
]


def upgrade():
    for table in ("ig_sources", "news_sources"):
        for name, coltype in _COLUMNS:
            op.add_column(table, sa.Column(name, coltype, nullable=True))


def downgrade():
    for table in ("ig_sources", "news_sources"):
        for name, _ in _COLUMNS:
            op.drop_column(table, name)
