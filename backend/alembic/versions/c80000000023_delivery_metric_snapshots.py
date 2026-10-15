"""delivery_metric_snapshots

Revision ID: c80000000023
Revises: c80000000022
"""

from alembic import context, op
import sqlalchemy as sa


revision = "c80000000023"
down_revision = "c80000000022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Add nullable delivery_id column with FK to publish_deliveries.id
    op.add_column(
        "post_metric_snapshots",
        sa.Column("delivery_id", sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        "fk_post_metric_snapshots_delivery_id",
        "post_metric_snapshots",
        "publish_deliveries",
        ["delivery_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_post_metric_snapshots_delivery_id",
        "post_metric_snapshots",
        ["delivery_id"],
    )

    # 2. Drop old full unique constraint on (publish_job_id, bucket)
    op.drop_constraint(
        "uq_post_metric_snapshots_job_bucket",
        "post_metric_snapshots",
        type_="unique",
    )

    # 3. Create partial unique index for legacy rows (where delivery_id IS NULL)
    op.create_index(
        "uq_post_metric_snapshots_legacy_job_bucket",
        "post_metric_snapshots",
        ["publish_job_id", "bucket"],
        unique=True,
        postgresql_where=sa.text("delivery_id IS NULL"),
        sqlite_where=sa.text("delivery_id IS NULL"),
    )

    # 4. Create partial unique index for delivery rows (where delivery_id IS NOT NULL)
    op.create_index(
        "uq_post_metric_snapshots_delivery_bucket",
        "post_metric_snapshots",
        ["delivery_id", "bucket"],
        unique=True,
        postgresql_where=sa.text("delivery_id IS NOT NULL"),
        sqlite_where=sa.text("delivery_id IS NOT NULL"),
    )


def downgrade() -> None:
    # 1. Check for duplicate (publish_job_id, bucket) metric snapshots across deliveries before any DDL.
    # Note on Offline SQL Mode: The Python precheck below runs during online execution (not context.is_offline_mode()).
    # Offline generated SQL cannot pre-inspect data, but remains protected by PostgreSQL transactional DDL:
    # if duplicate (publish_job_id, bucket) rows exist, ADD CONSTRAINT safely fails and rolls back the transaction.
    # Operators using offline scripts should keep the transaction and check duplicate groups manually beforehand,
    # or run online migration for descriptive refusal. Do not silently deduplicate metrics.
    bind = op.get_bind()
    if bind is not None and not context.is_offline_mode():
        dialect_name = getattr(getattr(bind, "dialect", None), "name", None)
        if dialect_name != "mock":
            result = bind.execute(
                sa.text(
                    "SELECT publish_job_id, bucket, COUNT(*) AS cnt "
                    "FROM post_metric_snapshots "
                    "GROUP BY publish_job_id, bucket "
                    "HAVING COUNT(*) > 1"
                )
            )
            duplicates = result.fetchall()
            if duplicates:
                details = ", ".join(
                    f"(job {r[0]}, bucket '{r[1]}': {r[2]} rows)" for r in duplicates[:5]
                )
                raise RuntimeError(
                    f"Cannot restore uq_post_metric_snapshots_job_bucket: "
                    f"found {len(duplicates)} duplicate (publish_job_id, bucket) metric snapshot group(s) "
                    f"across deliveries: {details}. "
                    "Preserving all metrics without silent deduplication; manual resolution required."
                )

    op.drop_index(
        "uq_post_metric_snapshots_delivery_bucket",
        table_name="post_metric_snapshots",
    )
    op.drop_index(
        "uq_post_metric_snapshots_legacy_job_bucket",
        table_name="post_metric_snapshots",
    )
    op.create_unique_constraint(
        "uq_post_metric_snapshots_job_bucket",
        "post_metric_snapshots",
        ["publish_job_id", "bucket"],
    )
    op.drop_index(
        "ix_post_metric_snapshots_delivery_id",
        table_name="post_metric_snapshots",
    )
    op.drop_constraint(
        "fk_post_metric_snapshots_delivery_id",
        "post_metric_snapshots",
        type_="foreignkey",
    )
    op.drop_column("post_metric_snapshots", "delivery_id")
