"""add scraper_backend to ig_sources

Revision ID: 8e9f0a1b2c3d
Revises: 7d8e9f0a1b2c
Create Date: 2026-06-28

"""
from alembic import op
import sqlalchemy as sa

revision = "8e9f0a1b2c3d"
down_revision = "7d8e9f0a1b2c"
branch_labels = None
depends_on = None

scraperbackend_enum = sa.Enum("auto", "instagrapi", "flashapi", name="scraperbackend")


def upgrade():
    scraperbackend_enum.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "ig_sources",
        sa.Column(
            "scraper_backend",
            scraperbackend_enum,
            nullable=False,
            server_default="auto",
        ),
    )


def downgrade():
    op.drop_column("ig_sources", "scraper_backend")
    scraperbackend_enum.drop(op.get_bind(), checkfirst=True)
