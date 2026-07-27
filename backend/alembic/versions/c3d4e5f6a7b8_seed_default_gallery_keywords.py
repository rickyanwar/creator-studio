"""seed default gallery keywords

Inserts the starter gallery keywords in app/seeds/default_gallery_keywords.json
(2026 MotoGP/F1 grids, UFC/boxing champions, NBA stars) so a fresh install /
server migration starts with them already present. Idempotent — skips
keywords that already exist.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-27
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c3d4e5f6a7b8"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade():
    from app.seeds import seed_default_gallery_keywords

    n = seed_default_gallery_keywords(op.get_bind())
    print(f"[seed] inserted {n} default gallery keyword(s)")


def downgrade():
    # Remove only the seeded keywords by name (leave any the user added since).
    import json
    import os

    from sqlalchemy import text

    seed_file = os.path.join(
        os.path.dirname(__file__), "..", "..", "app", "seeds", "default_gallery_keywords.json"
    )
    try:
        with open(seed_file, "r", encoding="utf-8") as f:
            keywords = [k["keyword"].strip().lower() for k in json.load(f) if k.get("keyword")]
    except Exception:
        keywords = []
    conn = op.get_bind()
    for keyword in keywords:
        conn.execute(text("DELETE FROM gallery_keywords WHERE keyword = :k"), {"k": keyword})
