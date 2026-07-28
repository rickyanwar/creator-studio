"""seed remaining News Highlight colour/alignment variants as defaults

These six templates ("News Highlight — Red · Left/Center", "Yellow ·
Left/Center", "Green · Left/Center") existed as fanpage-agnostic templates
but were never part of default_templates.json and were not flagged
is_default — so a fresh deploy never got them, and the flag didn't reflect
how they're actually used (shared, not per-fanpage). This migration seeds
them (idempotent — insert-if-missing-by-name, same as every other seed
migration) and flips is_default=true on any pre-existing row with the same
name so behaviour is identical on both a fresh DB and one that already has
them.

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-07-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "f7a8b9c0d1e2"
down_revision = "e6f7a8b9c0d1"
branch_labels = None
depends_on = None

_NAMES = [
    "News Highlight — Red · Left",
    "News Highlight — Red · Center",
    "News Highlight — Yellow · Left",
    "News Highlight — Green · Left",
    "News Highlight — Yellow · Center",
    "News Highlight — Green · Center",
]


def upgrade():
    from sqlalchemy import text
    from app.seeds import seed_default_templates

    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")

    conn = op.get_bind()
    conn.execute(
        text(
            "UPDATE design_templates SET is_default = true "
            "WHERE fanpage_id IS NULL AND name = ANY(:names)"
        ),
        {"names": _NAMES},
    )


def downgrade():
    from sqlalchemy import text

    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM design_templates WHERE name = ANY(:names) AND fanpage_id IS NULL"),
        {"names": _NAMES},
    )
