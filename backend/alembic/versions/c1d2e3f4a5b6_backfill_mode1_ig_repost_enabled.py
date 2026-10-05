"""backfill mode1_ig_repost_enabled for fanpages with active ig sources

Revision ID: c1d2e3f4a5b6
Revises: b3c4d5e6f8a9
Branch labels: None
Depends on: None

Data migration: set mode1_ig_repost_enabled = true for every fanpage that
already has ≥1 active fanpage_sources row whose ig_source is also active.
This preserves current behaviour — those fanpages are already receiving
Mode 1 posts (the column defaulted to true for new rows; older rows that
predate the column may have false/null depending on the server_default
applied during DDL).
"""

from alembic import op


revision = "c1d2e3f4a5b6"
down_revision = "b3c4d5e6f8a9"
branch_labels = None
depends_on = None


def upgrade():
    op.execute(
        """
        UPDATE target_fanpages
        SET    mode1_ig_repost_enabled = true
        WHERE  EXISTS (
            SELECT 1
            FROM   fanpage_sources  fs
            JOIN   ig_sources       igs ON igs.id = fs.ig_source_id
            WHERE  fs.fanpage_id  = target_fanpages.id
            AND    fs.is_active   = true
            AND    igs.is_active  = true
        )
        """
    )


def downgrade():
    # Intentional no-op: we cannot safely reverse a backfill because we
    # don't know which rows had mode1_ig_repost_enabled=false *before* the
    # upgrade ran (the column has a server_default of true, so any pre-
    # existing false value was an explicit admin choice we must not restore
    # blindly).  An operator who needs to disable Mode 1 on specific
    # fanpages should do so through the UI after rolling back the code.
    pass
