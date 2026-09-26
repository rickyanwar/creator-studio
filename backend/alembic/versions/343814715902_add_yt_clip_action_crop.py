"""add target_fanpages.yt_clip_action_crop (Mode 7: how shots without a face are framed)

"smart" (default): a full-height 9:16 crop that follows the on-screen action
(centred for camera motion such as a cockpit POV). "fit": the whole 16:9
frame over a blurred copy of itself. The user rejected blur-fit as the
default for racing footage (2026-09-26).

Revision ID: 343814715902
Revises: 8834717ee709
Create Date: 2026-09-26
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "343814715902"
down_revision = "8834717ee709"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "target_fanpages",
        sa.Column("yt_clip_action_crop", sa.String(length=8), nullable=False, server_default="smart"),
    )


def downgrade():
    op.drop_column("target_fanpages", "yt_clip_action_crop")
