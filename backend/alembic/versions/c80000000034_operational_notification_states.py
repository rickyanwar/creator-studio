"""operational_notification_states

Revision ID: c80000000034
Revises: c80000000033
"""

import logging
from alembic import op
import sqlalchemy as sa

revision = "c80000000034"
down_revision = "c80000000033"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

OLD_STATES = ("pending", "sent", "failed")
NEW_STATES = ("pending", "sent", "failed", "open", "resolved")


def upgrade() -> None:
    with op.batch_alter_table("operational_notifications") as batch_op:
        batch_op.drop_constraint("ck_operational_notifications_state", type_="check")
        batch_op.create_check_constraint(
            "ck_operational_notifications_state",
            "state IN ('pending', 'sent', 'failed', 'open', 'resolved')",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind is not None:
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "mock":
            res = bind.execute(
                sa.text(
                    "SELECT COUNT(*) FROM operational_notifications "
                    "WHERE state IN ('open', 'resolved')"
                )
            )
            count = res.scalar() if res is not None else 0
            if count and count > 0:
                logger.warning(
                    "Leaving wide ck_operational_notifications_state constraint: "
                    "%s rows use 'open'/'resolved'",
                    count,
                )
                return

    with op.batch_alter_table("operational_notifications") as batch_op:
        batch_op.drop_constraint("ck_operational_notifications_state", type_="check")
        batch_op.create_check_constraint(
            "ck_operational_notifications_state",
            "state IN ('pending', 'sent', 'failed')",
        )
