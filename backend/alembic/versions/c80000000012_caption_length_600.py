"""set caption_max_length to 600 and caption_hashtag_count to 3 with backup table

Revision ID: c80000000012
Revises: c80000000011
"""

from alembic import op
import sqlalchemy as sa


revision = "c80000000012"
down_revision = "c80000000011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Create backup table
    op.create_table(
        "caption_settings_backup_20261012",
        sa.Column("fanpage_id", sa.Integer(), primary_key=True),
        sa.Column("caption_max_length", sa.Integer(), nullable=True),
        sa.Column("caption_hashtag_count", sa.Integer(), nullable=True),
    )
    # Fill backup table with current values
    op.execute(
        "INSERT INTO caption_settings_backup_20261012 (fanpage_id, caption_max_length, caption_hashtag_count) "
        "SELECT id, caption_max_length, caption_hashtag_count FROM target_fanpages"
    )
    # 2. Update all target_fanpages rows
    op.execute(
        "UPDATE target_fanpages SET caption_max_length = 600, caption_hashtag_count = 3"
    )


def downgrade() -> None:
    # Restore values from backup table
    op.execute(
        "UPDATE target_fanpages tf "
        "SET caption_max_length = b.caption_max_length, "
        "    caption_hashtag_count = b.caption_hashtag_count "
        "FROM caption_settings_backup_20261012 b "
        "WHERE tf.id = b.fanpage_id"
    )
    # Drop backup table
    op.drop_table("caption_settings_backup_20261012")
