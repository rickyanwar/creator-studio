"""add per-fanpage toggle for Pinterest low-quality photo checks

Revision ID: a2f4c6e8b0d1
Revises: 343814715902
"""

import sqlalchemy as sa
from alembic import op

revision = "a2f4c6e8b0d1"
down_revision = "343814715902"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if not sa.inspect(bind).has_table("target_fanpages"):
        raise RuntimeError("target_fanpages table does not exist")
    columns = {column["name"] for column in sa.inspect(bind).get_columns("target_fanpages")}
    if "pinterest_allow_low_quality" not in columns:
        op.add_column(
            "target_fanpages",
            sa.Column("pinterest_allow_low_quality", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        )


def downgrade():
    # Column may predate this revision; dropping it could destroy user settings.
    pass
