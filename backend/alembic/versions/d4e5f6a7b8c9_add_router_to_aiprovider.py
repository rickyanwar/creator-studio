"""add router value to aiprovider enum

Adds '9Router' (OpenAI-compatible primary AI provider) to the aiprovider enum
so publish_jobs.ai_provider_used can record captions/news generated via 9Router.
Gemini and Groq remain as fallback providers.

Revision ID: d4e5f6a7b8c9
Revises: b7c8d9e0f1a2
Create Date: 2026-07-10
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "d4e5f6a7b8c9"
down_revision = "b7c8d9e0f1a2"
branch_labels = None
depends_on = None


def upgrade():
    # Postgres 12+ allows ADD VALUE inside a transaction; IF NOT EXISTS makes it idempotent.
    op.execute("ALTER TYPE aiprovider ADD VALUE IF NOT EXISTS 'router'")


def downgrade():
    # Postgres cannot drop an enum value without recreating the type; no-op.
    pass
