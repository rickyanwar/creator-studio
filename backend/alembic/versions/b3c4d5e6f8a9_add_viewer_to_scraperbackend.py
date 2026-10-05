"""add viewer value to scraperbackend enum

Revision ID: b3c4d5e6f8a9
Revises: a2f4c6e8b0d1
Branch labels: None
Depends on: None
"""

from alembic import op

revision = "b3c4d5e6f8a9"
down_revision = "a2f4c6e8b0d1"
branch_labels = None
depends_on = None


def upgrade():
    # ALTER TYPE requires autocommit; IF NOT EXISTS is idempotent on re-run.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE scraperbackend ADD VALUE IF NOT EXISTS 'viewer'")


def downgrade():
    # PostgreSQL cannot drop enum values — downgrade is intentionally a no-op.
    # To remove "viewer" you would need to recreate the type without it.
    pass
