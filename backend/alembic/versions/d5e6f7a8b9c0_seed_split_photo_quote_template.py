"""seed Quote Card (name badge) template

Re-runs seed_default_templates() (idempotent — inserts only templates from
default_templates.json that don't already exist by name) to add the new
"Quote Card (name badge)" template: single full-bleed photo, a name badge,
and the new "caption" field.

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d5e6f7a8b9c0"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade():
    from app.seeds import seed_default_templates

    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")


def downgrade():
    from sqlalchemy import text

    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
        {"n": "Quote Card (name badge)"},
    )
