"""seed Quote Card (word-highlight, name badge) template

Re-runs seed_default_templates() (idempotent — inserts only templates from
default_templates.json that don't already exist by name) to add
"Quote Card (word-highlight, name badge)": a copy of "Quote Card
(word-highlight)" with a name badge added behind the attribution subtitle,
coloured with the same word-highlight accent (#c7f754).

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0
Create Date: 2026-07-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e6f7a8b9c0d1"
down_revision = "d5e6f7a8b9c0"
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
        {"n": "Quote Card (word-highlight, name badge)"},
    )
