"""social_destination_platforms

Revision ID: c80000000033
Revises: c80000000032
"""

from alembic import op
import sqlalchemy as sa

revision = "c80000000033"
down_revision = "c80000000032"
branch_labels = None
depends_on = None

OLD_KINDS = ("facebook_profile", "facebook_page")
NEW_KINDS = (
    "facebook_profile",
    "facebook_page",
    "instagram",
    "youtube",
    "threads",
    "x",
)


def upgrade() -> None:
    # Drop and recreate ck_social_destinations_kind check constraint
    with op.batch_alter_table("social_destinations") as batch_op:
        batch_op.drop_constraint("ck_social_destinations_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_social_destinations_kind",
            "kind IN ('facebook_profile', 'facebook_page', 'instagram', 'youtube', 'threads', 'x')",
        )


def downgrade() -> None:
    # Restores the old list only if no rows use the new kinds
    conn = op.get_bind()
    new_kinds_sql = ", ".join(f"'{k}'" for k in ("instagram", "youtube", "threads", "x"))
    count = conn.execute(
        sa.text(f"SELECT COUNT(*) FROM social_destinations WHERE kind IN ({new_kinds_sql})")
    ).scalar()
    if count and count > 0:
        raise RuntimeError(
            f"Cannot downgrade: {count} social_destinations rows use new platform kinds"
        )

    with op.batch_alter_table("social_destinations") as batch_op:
        batch_op.drop_constraint("ck_social_destinations_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_social_destinations_kind",
            "kind IN ('facebook_profile', 'facebook_page')",
        )
