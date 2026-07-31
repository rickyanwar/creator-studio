"""add rendering/publishing values to publishjobstatus enum

Adds in-progress lease states so the design-render and publish tasks can
atomically claim a job (UPDATE ... WHERE status = 'pending_design'/
'pending_publish') before doing the real render/API call, and so the beat
sweeps (render_pending_designs) stop re-matching a job that's already being
worked on. Fixes duplicate renders/publishes caused by a job being picked up
twice while its previous run was still in flight.

Revision ID: b7f3a9c1d5e2
Revises: 2c8f4a1e6b3d
Create Date: 2026-07-31
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b7f3a9c1d5e2"
down_revision = "2c8f4a1e6b3d"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres 12+ allows ADD VALUE inside a transaction; IF NOT EXISTS makes it idempotent.
    op.execute("ALTER TYPE publishjobstatus ADD VALUE IF NOT EXISTS 'rendering'")
    op.execute("ALTER TYPE publishjobstatus ADD VALUE IF NOT EXISTS 'publishing'")


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op.
    pass
