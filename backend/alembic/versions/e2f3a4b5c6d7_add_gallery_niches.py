"""add mode2_gallery_niches to target_fanpages, backfill from existing keywords

Image matching for a fanpage now resolves through GalleryKeyword.niche
instead of a manually curated keyword list — see
app.services.design_images.niche_keywords. Backfills each fanpage's niches
by looking up which niche(s) its currently-configured keywords belong to.

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-07-29
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("mode2_gallery_niches", sa.ARRAY(sa.String()), nullable=False, server_default="{}"),
    )

    conn = op.get_bind()
    conn.execute(sa.text("""
        UPDATE target_fanpages fp
        SET mode2_gallery_niches = sub.niches
        FROM (
            SELECT fp2.id AS fanpage_id, array_agg(DISTINCT gk.niche) AS niches
            FROM target_fanpages fp2
            CROSS JOIN LATERAL unnest(fp2.mode2_gallery_keywords) AS kw(keyword)
            JOIN gallery_keywords gk ON lower(gk.keyword) = lower(kw.keyword)
            WHERE gk.niche IS NOT NULL
            GROUP BY fp2.id
        ) sub
        WHERE fp.id = sub.fanpage_id
    """))


def downgrade():
    op.drop_column("target_fanpages", "mode2_gallery_niches")
