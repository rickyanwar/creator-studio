"""Mode 5: drop pinterest_render_style — reuses default_quote_template_id /
default_news_template_id instead, picked per idea by whether the bound
photo has a detected face (see render_pinterest)

Revision ID: 2c8a214c0b39
Revises: 97cd344f1a3f
Create Date: 2026-08-25
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "2c8a214c0b39"
down_revision = "97cd344f1a3f"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_column("target_fanpages", "pinterest_render_style")


def downgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("pinterest_render_style", sa.String(length=16), nullable=False, server_default="news"),
    )
