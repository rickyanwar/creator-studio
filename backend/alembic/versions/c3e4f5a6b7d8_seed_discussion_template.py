"""seed Discussion Card template (Mode 4)

Inserts the shared "Discussion Card (badge + big question)" template from
default_templates.json and tags it category='discussion'. seed_default_templates()
itself does not set category (the original backfill migration f3a4b5c6d7e8 already
ran and won't touch a newly-added name), so we set it here explicitly.

Revision ID: c3e4f5a6b7d8
Revises: b2d3f4e5a6c7
Create Date: 2026-08-09
"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "c3e4f5a6b7d8"
down_revision = "b2d3f4e5a6c7"
branch_labels = None
depends_on = None

_NAME = "Discussion Card (badge + big question)"


def upgrade():
    from app.seeds import seed_default_templates

    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")

    # Tag the discussion category (seed_default_templates never sets it).
    op.get_bind().execute(
        text("UPDATE design_templates SET category = 'discussion' WHERE name = :n AND fanpage_id IS NULL"),
        {"n": _NAME},
    )


def downgrade():
    op.get_bind().execute(
        text("DELETE FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
        {"n": _NAME},
    )
