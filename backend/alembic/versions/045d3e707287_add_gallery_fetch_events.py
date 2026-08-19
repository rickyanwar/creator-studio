"""add gallery_fetch_events table

Logs one row per paid 9Router web/fetch (jina-reader) call — see
app.models.gallery_fetch_events and image_downloader._9router_fetch_markdown
— so actual daily Jina spend is queryable instead of estimated, following
2026-08-20's gallery-download throttling work.

Revision ID: 045d3e707287
Revises: 8654b4bc91dc
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "045d3e707287"
down_revision = "8654b4bc91dc"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "gallery_fetch_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("context", sa.String(32), nullable=False),
        sa.Column("keyword", sa.String(128), nullable=True),
        sa.Column("niche", sa.String(64), nullable=True),
        sa.Column("url", sa.String(1024), nullable=True),
        sa.Column("success", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
    )
    op.create_index("ix_gallery_fetch_events_created_at", "gallery_fetch_events", ["created_at"])
    op.create_index("ix_gallery_fetch_events_context", "gallery_fetch_events", ["context"])
    op.create_index("ix_gallery_fetch_events_keyword", "gallery_fetch_events", ["keyword"])
    op.create_index("ix_gallery_fetch_events_success", "gallery_fetch_events", ["success"])


def downgrade():
    op.drop_table("gallery_fetch_events")
