"""raise gallery_keywords.max_pages/max_images defaults (10->20, 50->500)

Part of the usage-aware gallery scheduling rework (2026-08-17): the scheduled
sweep (download_all_keywords) no longer gates on max_images directly — an
actively-newsworthy keyword (e.g. a rider racing every week) now grows toward
a much higher active-image ceiling in code (_ACTIVE_KEYWORD_CEILING) instead
of the old flat 50, and the early-stop-on-consecutive-dupes logic already
keeps a caught-up keyword's per-run cost low, so the old 10-page cap was the
thing actually limiting how deep a catching-up keyword could reach in one
run, not cost. max_images itself is now only the ceiling for an explicit
"Download Now" — raised too so that isn't left arbitrarily more restrictive
than the scheduled sweep. Bumps both column defaults for new keywords, and
any existing keyword still sitting at the old defaults up to the new ones —
keywords deliberately configured to something else are left untouched.

Revision ID: 507e8c06f625
Revises: c9d0e2f3a4b5
Create Date: 2026-08-17
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "507e8c06f625"
down_revision = "c9d0e2f3a4b5"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE gallery_keywords ALTER COLUMN max_pages SET DEFAULT 20")
    op.execute("UPDATE gallery_keywords SET max_pages = 20 WHERE max_pages = 10")
    op.execute("ALTER TABLE gallery_keywords ALTER COLUMN max_images SET DEFAULT 500")
    op.execute("UPDATE gallery_keywords SET max_images = 500 WHERE max_images = 50")


def downgrade():
    op.execute("ALTER TABLE gallery_keywords ALTER COLUMN max_pages SET DEFAULT 10")
    op.execute("UPDATE gallery_keywords SET max_pages = 10 WHERE max_pages = 20")
    op.execute("ALTER TABLE gallery_keywords ALTER COLUMN max_images SET DEFAULT 50")
    op.execute("UPDATE gallery_keywords SET max_images = 50 WHERE max_images = 500")
