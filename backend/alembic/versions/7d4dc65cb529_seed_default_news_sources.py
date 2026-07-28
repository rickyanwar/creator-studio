"""seed default news sources

Inserts the news sources in app/seeds/default_news_sources.json (verified
against the live sites) so a fresh install starts with a working set of
scrapable MotoGP/F1/UFC sources instead of none. Idempotent — matched by
category_url, skips ones that already exist.

Revision ID: 7d4dc65cb529
Revises: ea233a018626
Create Date: 2026-07-29
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "7d4dc65cb529"
down_revision = "ea233a018626"
branch_labels = None
depends_on = None


def upgrade():
    from app.seeds import seed_default_news_sources

    n = seed_default_news_sources(op.get_bind())
    print(f"[seed] inserted {n} default news source(s)")


def downgrade():
    import json
    import os

    from sqlalchemy import text

    seed_file = os.path.join(os.path.dirname(__file__), "..", "..", "app", "seeds", "default_news_sources.json")
    try:
        with open(seed_file, "r", encoding="utf-8") as f:
            urls = [s["category_url"] for s in json.load(f) if s.get("category_url")]
    except Exception:
        urls = []
    conn = op.get_bind()
    for url in urls:
        conn.execute(text("DELETE FROM news_sources WHERE category_url = :u"), {"u": url})
