"""quarantine 2 confirmed wrong-identity gallery photos

User report (2026-09-25): job 9069's "Ilia Topuria" split photo was
actually Real Madrid footballer Fede Valverde. Confirmed live against
production: gallery_images.id=15879 is tagged keyword="ilia topuria" but
its source_image_url is a Getty caption for "footballer Fede Valverde".
A quick sweep of the same subject cluster found a second confirmed
mismatch: id=33676 tagged keyword="usman nurmagomedov" but its Getty
caption says "Muhammad Naimov" (a different fighter).

Root cause (see services/design_images.py vision_verify_subject /
_filter_verified_subject): the identity-verification vision call already
exists and runs on both fresh-fetch and gallery-reuse paths, but a
false-positive "match: true" from the vision model slipped both of these
through — and the model's own self-reported `confidence` score was
computed but never actually checked by any caller (separate code fix
alongside this migration adds a minimum-confidence floor). This migration
only quarantines the 2 already-confirmed-bad rows found so far — it is
NOT a full-table re-verification sweep (that would need a new vision pass
over every existing photo, out of scope here).

Soft-delete (is_deleted=true) rather than hard delete — same convention
GalleryImage already uses elsewhere — so the row/local file survive for
audit but future gallery-reuse lookups (which filter on is_deleted=false)
never pick them again.

Revision ID: 60fffb49b7b8
Revises: fb264fbdf01f
Create Date: 2026-09-25
"""

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "60fffb49b7b8"
down_revision = "fb264fbdf01f"
branch_labels = None
depends_on = None

_BAD_IDS = [15879, 33676]


def upgrade():
    conn = op.get_bind()
    conn.execute(
        text("UPDATE gallery_images SET is_deleted = true WHERE id = ANY(:ids)"),
        {"ids": _BAD_IDS},
    )


def downgrade():
    conn = op.get_bind()
    conn.execute(
        text("UPDATE gallery_images SET is_deleted = false WHERE id = ANY(:ids)"),
        {"ids": _BAD_IDS},
    )
