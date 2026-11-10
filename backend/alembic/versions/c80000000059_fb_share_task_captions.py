"""Per-group short caption variants for Group Share tasks."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c80000000059"
down_revision = "c80000000058"
branch_labels = None
depends_on = None


def upgrade():
    # New table only, no foreign keys: never wait long on a lock held by a busy worker.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET lock_timeout = '15s'")
    op.create_table(
        "fb_share_task_captions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column(
            "hashtags",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_fb_share_task_captions_task_id", "fb_share_task_captions", ["task_id"], unique=True)


def downgrade():
    op.drop_table("fb_share_task_captions")
