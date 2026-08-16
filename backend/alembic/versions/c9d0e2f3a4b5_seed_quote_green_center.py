"""seed Quote Card — Green · Center

New quote-category template (user request, 2026-08-16): same layout as
"Discussion Card — Green · Center" (image/scrim/title, green accent
#5af905, Poppins bold, center-aligned) but for the "quote" category instead
of "discussion" — no label/labelBadge (no DISCUSSION/HOT TAKE pill), just
the big decorative quote mark (placeholderRole=quoteIcon, same Anton-font
glyph/gap as "Quote Card (name badge)") above the headline. Deliberately
omits that template's name-badge + caption elements — user asked for "only
the quote mark".

Revision ID: c9d0e2f3a4b5
Revises: b8c9d0e2f3a4
Create Date: 2026-08-16
"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "c9d0e2f3a4b5"
down_revision = "b8c9d0e2f3a4"
branch_labels = None
depends_on = None

_NAME = "Quote Card — Green · Center"


def upgrade():
    from app.seeds import seed_default_templates

    n = seed_default_templates(op.get_bind())
    print(f"[seed] inserted {n} default design template(s)")

    op.get_bind().execute(
        text("UPDATE design_templates SET category = 'quote' WHERE name = :n AND fanpage_id IS NULL"),
        {"n": _NAME},
    )


def downgrade():
    op.get_bind().execute(
        text("DELETE FROM design_templates WHERE name = :n AND fanpage_id IS NULL"),
        {"n": _NAME},
    )
