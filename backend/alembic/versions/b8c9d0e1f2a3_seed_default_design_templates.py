"""seed default design templates

Inserts the shared design templates in app/seeds/default_templates.json so a
fresh install / server migration starts with the default design already
present. Idempotent — skips templates that already exist.

Revision ID: b8c9d0e1f2a3
Revises: a7b8c9d0e1f2
Create Date: 2026-07-10
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "b8c9d0e1f2a3"
down_revision = "a7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade():
    from app.seeds import seed_default_templates

    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")


def downgrade():
    # Remove only the seeded shared templates by name (leave user-created ones).
    import json
    import os

    from sqlalchemy import text

    seed_file = os.path.join(os.path.dirname(__file__), "..", "..", "app", "seeds", "default_templates.json")
    try:
        with open(seed_file, "r", encoding="utf-8") as f:
            names = [t["name"] for t in json.load(f) if t.get("name")]
    except Exception:
        names = []
    conn = op.get_bind()
    for name in names:
        conn.execute(
            text("DELETE FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
            {"n": name},
        )
