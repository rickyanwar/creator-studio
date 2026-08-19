"""add nine_router_discussion_model to settings

Lets Mode 4 discussion/hot-take copy (news_copywriter.generate_discussion_copy
/ factcheck_discussion_claim) use a stronger model/combo route than the
general nine_router_model, editable from the Settings UI so it can be
swapped back if it misbehaves. NULL falls back to the built-in default
("smart-combo") — see app/services/nine_router.py.

Revision ID: e10eb322bf3e
Revises: 045d3e707287
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "e10eb322bf3e"
down_revision = "045d3e707287"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("settings", sa.Column("nine_router_discussion_model", sa.String(128), nullable=True))


def downgrade():
    op.drop_column("settings", "nine_router_discussion_model")
