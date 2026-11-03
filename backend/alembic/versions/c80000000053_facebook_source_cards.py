"""Add Facebook source card settings and staged photo provenance.

Revision ID: c80000000053
Revises: c80000000052
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000053"
down_revision = "c80000000052"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("facebook_photo_sources", sa.Column("format", sa.String(16), nullable=True))
    op.add_column("facebook_photo_sources", sa.Column("chatgpt_edit_prompt", sa.Text(), nullable=True))
    op.add_column("facebook_photo_sources", sa.Column("last_fetched_at", sa.DateTime(), nullable=True))
    op.add_column("facebook_photo_ideas", sa.Column("source_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_facebook_photo_ideas_source_id", "facebook_photo_ideas",
        "facebook_photo_sources", ["source_id"], ["id"], ondelete="SET NULL",
    )
    op.create_index("ix_facebook_photo_ideas_source_id", "facebook_photo_ideas", ["source_id"])
    op.add_column("facebook_photo_ideas", sa.Column("image_local_path", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_constraint("fk_facebook_photo_ideas_source_id", "facebook_photo_ideas", type_="foreignkey")
    op.drop_index("ix_facebook_photo_ideas_source_id", table_name="facebook_photo_ideas")
    op.drop_column("facebook_photo_ideas", "image_local_path")
    op.drop_column("facebook_photo_ideas", "source_id")
    op.drop_column("facebook_photo_sources", "last_fetched_at")
    op.drop_column("facebook_photo_sources", "chatgpt_edit_prompt")
    op.drop_column("facebook_photo_sources", "format")
