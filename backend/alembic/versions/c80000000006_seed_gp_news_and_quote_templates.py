"""seed GP News Breaking and GP Quote templates

Revision ID: c80000000006
Revises: c80000000005
Create Date: 2026-10-12
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "c80000000006"
down_revision = "c80000000005"
branch_labels = None
depends_on = None


def upgrade():
    from app.seeds import seed_default_templates
    from sqlalchemy import text

    conn = op.get_bind()
    n = seed_default_templates(conn)
    print(f"[seed] inserted {n} default design template(s)")
    conn.execute(
        text("UPDATE design_templates SET category = 'news' WHERE name = 'GP News — Breaking' AND fanpage_id IS NULL")
    )
    conn.execute(
        text("UPDATE design_templates SET category = 'quote' WHERE name = 'GP Quote' AND fanpage_id IS NULL")
    )


def downgrade():
    from sqlalchemy import text

    conn = op.get_bind()
    conn.execute(
        text("DELETE FROM design_templates WHERE name IN ('GP News — Breaking', 'GP Quote') AND fanpage_id IS NULL")
    )
