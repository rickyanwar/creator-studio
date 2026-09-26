"""add target_fanpages.facebook_photo_topic_filter (Mode 6 per-fanpage topic filter)

Optional free text (e.g. "Formula 1 and motorsport only"). When set, Mode 6
skips source photos whose post isn't actually about that topic — judged in
the same vision call that reads the photo. NULL/empty = no filter, for
fanpages that aren't tied to one niche.

Revision ID: 8834717ee709
Revises: 382d8240c9c4
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "8834717ee709"
down_revision = "382d8240c9c4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("target_fanpages", sa.Column("facebook_photo_topic_filter", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("target_fanpages", "facebook_photo_topic_filter")
