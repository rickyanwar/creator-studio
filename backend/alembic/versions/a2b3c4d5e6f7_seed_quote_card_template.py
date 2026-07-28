"""seed Quote Card template

Re-runs seed_default_templates() (idempotent — inserts only templates from
default_templates.json that don't already exist by name) to add the new
"Quote Card (word-highlight)" template alongside the existing MotoGP one.

Revision ID: a2b3c4d5e6f7
Revises: f1a2b3c4d5e6
Create Date: 2026-07-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "a2b3c4d5e6f7"
down_revision = "f1a2b3c4d5e6"
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
        {"n": "Quote Card (word-highlight)"},
    )
