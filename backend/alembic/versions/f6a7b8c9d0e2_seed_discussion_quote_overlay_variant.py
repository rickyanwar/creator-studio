"""seed Discussion Card — Green · Center (Quote overlay) variant

Copies the "News Highlight — Green · Center (Quote overlay)" layout
(distinct from the plain "News Highlight — Green · Center" already used for
the other Green discussion card — different scrim gradient, Tusker Grotesk
typography instead of Poppins, lighter accent #c7f754) into Mode 4, with the
discussion badge colour matched to its own accent per the established rule.

Revision ID: f6a7b8c9d0e2
Revises: e5f6a7b8c9d1
Create Date: 2026-08-16
"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "f6a7b8c9d0e2"
down_revision = "e5f6a7b8c9d1"
branch_labels = None
depends_on = None

_NAME = "Discussion Card — Green · Center (Quote overlay)"


def upgrade():
    from app.seeds import seed_default_templates

    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")

    op.get_bind().execute(
        text("UPDATE design_templates SET category = 'discussion' WHERE name = :n AND fanpage_id IS NULL"),
        {"n": _NAME},
    )


def downgrade():
    op.get_bind().execute(
        text("DELETE FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
        {"n": _NAME},
    )
