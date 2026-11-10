"""Hidden group-lane fanpage per brand for Group Share."""

from alembic import op
import sqlalchemy as sa

revision = "c80000000058"
down_revision = "c80000000057"
branch_labels = None
depends_on = None


def upgrade():
    # New table only, no foreign keys: never wait long on a lock held by a busy worker.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET lock_timeout = '15s'")
    op.create_table(
        "fb_share_lanes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("brand_fanpage_id", sa.Integer(), nullable=False),
        sa.Column("lane_fanpage_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_fb_share_lanes_brand_fanpage_id", "fb_share_lanes", ["brand_fanpage_id"], unique=True)
    op.create_index("ix_fb_share_lanes_lane_fanpage_id", "fb_share_lanes", ["lane_fanpage_id"], unique=True)


def downgrade():
    op.drop_table("fb_share_lanes")
