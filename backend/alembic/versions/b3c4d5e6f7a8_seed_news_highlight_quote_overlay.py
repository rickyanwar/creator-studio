"""seed News Highlight (Quote overlay) template

Re-runs seed_default_templates() (idempotent — inserts only templates from
default_templates.json that don't already exist by name) to add the new
"News Highlight — Green · Center (Quote overlay)" template: News Highlight's
layout/sizing with Quote Card's font (Tusker Grotesk) and overlay (scrim).

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-07-28
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b3c4d5e6f7a8"
down_revision = "a2b3c4d5e6f7"
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
        {"n": "News Highlight — Green · Center (Quote overlay)"},
    )
