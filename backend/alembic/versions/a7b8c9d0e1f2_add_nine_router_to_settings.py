"""add 9Router config to settings

Lets the 9Router base URL / API key / model be configured from the web UI
(overriding the NINE_ROUTER_* env vars). API key is stored encrypted.

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-07-10
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "a7b8c9d0e1f2"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("settings", sa.Column("nine_router_base_url", sa.String(length=256), nullable=True))
    op.add_column("settings", sa.Column("nine_router_api_key_encrypted", sa.String(length=512), nullable=True))
    op.add_column("settings", sa.Column("nine_router_model", sa.String(length=128), nullable=True))


def downgrade():
    op.drop_column("settings", "nine_router_model")
    op.drop_column("settings", "nine_router_api_key_encrypted")
    op.drop_column("settings", "nine_router_base_url")
