import io
from unittest.mock import MagicMock, patch, call
import pytest
from alembic.command import upgrade, downgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.database import Base
import app.models as models
from app.models.social_publishing import (
    SocialDestination,
    PhoneDevice,
    DestinationDeviceAssignment,
    PublishDelivery,
    DeliveryEvent,
    DeviceLease,
    RecoveryIncident,
    OperationalNotification,
)
from app.models.target_fanpages import TargetFanpage
from app.models.publish_jobs import PublishJob


def test_models_registered_in_metadata():
    """Verify all new models are registered in Base.metadata and exported in app.models."""
    expected_tables = {
        "social_destinations",
        "phone_devices",
        "destination_device_assignments",
        "publish_deliveries",
        "delivery_events",
        "device_leases",
        "recovery_incidents",
        "operational_notifications",
    }
    assert expected_tables.issubset(Base.metadata.tables.keys())

    # Check export in app.models
    for cls in [
        SocialDestination,
        PhoneDevice,
        DestinationDeviceAssignment,
        PublishDelivery,
        DeliveryEvent,
        DeviceLease,
        RecoveryIncident,
        OperationalNotification,
    ]:
        assert cls.__name__ in models.__all__
        assert getattr(models, cls.__name__) is cls

    # Check TargetFanpage.repliz_account_id is nullable
    col = TargetFanpage.__table__.columns["repliz_account_id"]
    assert col.nullable is True
    assert "created_at" in TargetFanpage.__table__.columns
    assert "updated_at" in TargetFanpage.__table__.columns

    # Check relationships
    assert hasattr(TargetFanpage, "social_destinations")
    assert hasattr(PublishJob, "deliveries")


def test_model_constraints_and_indexes():
    """Verify check constraints, unique constraints, and indexes on social models."""
    sd = SocialDestination.__table__
    sd_checks = {c.name for c in sd.constraints if hasattr(c, "sqltext")}
    assert "ck_social_destinations_kind" in sd_checks
    assert "ck_social_destinations_mode" in sd_checks
    assert "ck_social_destinations_daily_target_pos" in sd_checks
    assert "ck_social_destinations_phone_target_nonneg" in sd_checks
    assert "ck_social_destinations_repliz_target_nonneg" in sd_checks
    assert "ck_social_destinations_min_gap_pos" in sd_checks

    sd_indexes = {ix.name for ix in sd.indexes}
    assert "ix_social_destinations_project_kind_parent" in sd_indexes
    assert sd.columns["repliz_account_id"].unique is True

    # DestinationDeviceAssignment
    dda = DestinationDeviceAssignment.__table__
    dda_uniques = {u.name for u in dda.constraints if getattr(u, "columns", None) is not None and getattr(u, "name", None)}
    assert "uq_destination_device" in dda_uniques
    assert "uq_destination_priority" not in dda_uniques
    dda_indexes = {ix.name for ix in dda.indexes}
    assert "uq_destination_priority" in dda_indexes
    priority_idx = next(ix for ix in dda.indexes if ix.name == "uq_destination_priority")
    assert priority_idx.unique is True

    # PublishDelivery
    pd = PublishDelivery.__table__
    pd_uniques = {u.name for u in pd.constraints if getattr(u, "columns", None) is not None and getattr(u, "name", None)}
    assert "uq_publish_job_destination" in pd_uniques
    assert "uq_delivery_destination_facebook_post" in pd_uniques
    pd_checks = {c.name for c in pd.constraints if hasattr(c, "sqltext")}
    assert "ck_publish_deliveries_media_kind" in pd_checks
    for col_name in [
        "caption_snapshot",
        "media_snapshot",
        "media_kind",
        "title_snapshot",
        "alt_snapshot",
        "content_type_snapshot",
    ]:
        assert col_name in pd.columns
        assert pd.columns[col_name].nullable is True
    pd_indexes = {ix.name for ix in pd.indexes}
    assert "ix_publish_deliveries_dest_state_scheduled" in pd_indexes

    # DeviceLease PK & unique
    dl = DeviceLease.__table__
    assert dl.primary_key.columns.keys() == ["device_id"]
    assert dl.columns["delivery_id"].unique is True

    # OperationalNotification
    opn = OperationalNotification.__table__
    assert opn.columns["dedupe_key"].unique is True


def test_alembic_revision_head():
    """Verify migration revision lineage and head."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    assert script.get_heads() == ["c80000000025"]

    rev25 = script.get_revision("c80000000025")
    assert rev25.down_revision == "c80000000024"

    rev24 = script.get_revision("c80000000024")
    assert rev24.down_revision == "c80000000023"

    rev23 = script.get_revision("c80000000023")
    assert rev23.down_revision == "c80000000022"

    rev22 = script.get_revision("c80000000022")
    assert rev22.down_revision == "c80000000021"

    rev21 = script.get_revision("c80000000021")
    assert rev21.down_revision == "c80000000020"


def test_migration_upgrade_and_downgrade_offline_sql(capsys):
    """Verify offline static SQL generation for upgrade and downgrade."""
    config = Config("alembic.ini")

    # Upgrade SQL
    upgrade(config, "c80000000020:c80000000021", sql=True)
    up_sql, _ = capsys.readouterr()

    assert "ALTER TABLE target_fanpages ALTER COLUMN repliz_account_id DROP NOT NULL;" in up_sql
    assert "CREATE TABLE phone_devices" in up_sql
    assert "CREATE TABLE social_destinations" in up_sql
    assert "CREATE TABLE destination_device_assignments" in up_sql
    assert "CREATE TABLE publish_deliveries" in up_sql
    assert "CREATE TABLE delivery_events" in up_sql
    assert "CREATE TABLE device_leases" in up_sql
    assert "CREATE TABLE recovery_incidents" in up_sql
    assert "CREATE TABLE operational_notifications" in up_sql
    assert "INSERT INTO social_destinations" in up_sql
    assert "ON CONFLICT (repliz_account_id) DO NOTHING" in up_sql
    assert "CREATE UNIQUE INDEX uq_destination_priority ON destination_device_assignments (destination_id, priority) WHERE is_active = true;" in up_sql
    assert "NOW() AS created_at" not in up_sql
    assert "NOW() AS updated_at" not in up_sql
    assert "created_at,\n            updated_at\n        FROM target_fanpages" in up_sql

    # Downgrade SQL
    downgrade(config, "c80000000021:c80000000020", sql=True)
    down_sql, _ = capsys.readouterr()

    assert "DROP TABLE operational_notifications;" in down_sql
    assert "DROP TABLE recovery_incidents;" in down_sql
    assert "DROP TABLE device_leases;" in down_sql
    assert "DROP TABLE delivery_events;" in down_sql
    assert "DROP TABLE publish_deliveries;" in down_sql
    assert "DROP TABLE destination_device_assignments;" in down_sql
    assert "DROP TABLE social_destinations;" in down_sql
    assert "DROP TABLE phone_devices;" in down_sql
    assert "ALTER TABLE target_fanpages ALTER COLUMN repliz_account_id SET NOT NULL;" in down_sql


def test_delivery_snapshots_migration_offline_sql(capsys):
    """Verify offline static SQL generation for c80000000022 delivery snapshots."""
    config = Config("alembic.ini")

    # Upgrade SQL
    upgrade(config, "c80000000021:c80000000022", sql=True)
    up_sql, _ = capsys.readouterr()

    assert "ALTER TABLE publish_deliveries ADD COLUMN caption_snapshot TEXT;" in up_sql
    assert "ALTER TABLE publish_deliveries ADD COLUMN media_snapshot JSON;" in up_sql
    assert "ALTER TABLE publish_deliveries ADD COLUMN media_kind VARCHAR(16);" in up_sql
    assert "ALTER TABLE publish_deliveries ADD COLUMN title_snapshot TEXT;" in up_sql
    assert "ALTER TABLE publish_deliveries ADD COLUMN alt_snapshot TEXT;" in up_sql
    assert "ALTER TABLE publish_deliveries ADD COLUMN content_type_snapshot VARCHAR(32);" in up_sql
    assert "ALTER TABLE publish_deliveries ADD CONSTRAINT ck_publish_deliveries_media_kind CHECK (media_kind IS NULL OR media_kind IN ('image', 'album', 'video'));" in up_sql

    # Downgrade SQL
    downgrade(config, "c80000000022:c80000000021", sql=True)
    down_sql, _ = capsys.readouterr()

    assert "ALTER TABLE publish_deliveries DROP CONSTRAINT ck_publish_deliveries_media_kind;" in down_sql
    assert "ALTER TABLE publish_deliveries DROP COLUMN caption_snapshot;" in down_sql
    assert "ALTER TABLE publish_deliveries DROP COLUMN media_snapshot;" in down_sql
    assert "ALTER TABLE publish_deliveries DROP COLUMN media_kind;" in down_sql
    assert "ALTER TABLE publish_deliveries DROP COLUMN title_snapshot;" in down_sql
    assert "ALTER TABLE publish_deliveries DROP COLUMN alt_snapshot;" in down_sql
    assert "ALTER TABLE publish_deliveries DROP COLUMN content_type_snapshot;" in down_sql


def test_delivery_metric_snapshots_migration_offline_sql(capsys):
    """Verify offline static SQL generation for c80000000023 delivery metric snapshots."""
    config = Config("alembic.ini")

    # Upgrade SQL
    upgrade(config, "c80000000022:c80000000023", sql=True)
    up_sql, _ = capsys.readouterr()

    assert "ALTER TABLE post_metric_snapshots ADD COLUMN delivery_id INTEGER;" in up_sql
    assert "CREATE INDEX ix_post_metric_snapshots_delivery_id ON post_metric_snapshots (delivery_id);" in up_sql
    assert "ALTER TABLE post_metric_snapshots DROP CONSTRAINT uq_post_metric_snapshots_job_bucket;" in up_sql
    assert "CREATE UNIQUE INDEX uq_post_metric_snapshots_legacy_job_bucket ON post_metric_snapshots (publish_job_id, bucket) WHERE delivery_id IS NULL;" in up_sql
    assert "CREATE UNIQUE INDEX uq_post_metric_snapshots_delivery_bucket ON post_metric_snapshots (delivery_id, bucket) WHERE delivery_id IS NOT NULL;" in up_sql

    # Downgrade SQL
    downgrade(config, "c80000000023:c80000000022", sql=True)
    down_sql, _ = capsys.readouterr()

    assert "DROP INDEX uq_post_metric_snapshots_delivery_bucket;" in down_sql
    assert "DROP INDEX uq_post_metric_snapshots_legacy_job_bucket;" in down_sql
    assert "ALTER TABLE post_metric_snapshots ADD CONSTRAINT uq_post_metric_snapshots_job_bucket UNIQUE (publish_job_id, bucket);" in down_sql
    assert "DROP INDEX ix_post_metric_snapshots_delivery_id;" in down_sql
    assert "ALTER TABLE post_metric_snapshots DROP COLUMN delivery_id;" in down_sql


def test_migration_downgrade_aborts_on_null_repliz_id():
    """Verify online downgrade raises RuntimeError when NULL repliz_account_id exists."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000021").module

    mock_bind = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 2  # 2 profile projects exist
    mock_bind.execute.return_value = mock_result

    with patch.object(migration.op, "get_bind", return_value=mock_bind), \
         patch.object(migration.context, "is_offline_mode", return_value=False):
        with pytest.raises(RuntimeError, match="profile-only projects have NULL repliz_account_id"):
            migration.downgrade()


def test_migration_downgrade_succeeds_when_no_null_repliz_id():
    """Verify online downgrade drops tables in FK-safe reverse order and sets NOT NULL when no nulls exist."""
    import sqlalchemy as sa
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000021").module

    mock_bind = MagicMock()
    mock_result = MagicMock()
    mock_result.scalar.return_value = 0
    mock_bind.execute.return_value = mock_result

    with patch.object(migration.op, "get_bind", return_value=mock_bind), \
         patch.object(migration.context, "is_offline_mode", return_value=False), \
         patch.object(migration.op, "drop_table") as mock_drop, \
         patch.object(migration.op, "alter_column") as mock_alter:
        migration.downgrade()
        dropped = [call.args[0] for call in mock_drop.call_args_list]
        assert dropped == [
            "operational_notifications",
            "recovery_incidents",
            "device_leases",
            "delivery_events",
            "publish_deliveries",
            "destination_device_assignments",
            "social_destinations",
            "phone_devices",
        ]
        assert mock_alter.call_count == 1
        assert mock_alter.call_args[0][0] == "target_fanpages"
        assert mock_alter.call_args[0][1] == "repliz_account_id"
        assert mock_alter.call_args[1]["nullable"] is False


def test_c23_downgrade_aborts_on_duplicate_job_bucket_metrics():
    """Verify c23 downgrade precheck aborts before ANY schema mutation when duplicates exist."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000023").module

    mock_bind = MagicMock()
    mock_result = MagicMock()
    # Simulate duplicate (publish_job_id, bucket) metric snapshots across deliveries
    mock_result.fetchall.return_value = [(101, "1h", 2)]
    mock_bind.execute.return_value = mock_result

    with patch.object(migration.op, "get_bind", return_value=mock_bind), \
         patch.object(migration.context, "is_offline_mode", return_value=False), \
         patch.object(migration.op, "drop_index") as mock_drop_index, \
         patch.object(migration.op, "create_unique_constraint") as mock_create_uq, \
         patch.object(migration.op, "drop_constraint") as mock_drop_constraint, \
         patch.object(migration.op, "drop_column") as mock_drop_column:

        with pytest.raises(RuntimeError, match="Cannot restore uq_post_metric_snapshots_job_bucket"):
            migration.downgrade()

        # Precheck must prevent ANY schema mutation
        mock_drop_index.assert_not_called()
        mock_create_uq.assert_not_called()
        mock_drop_constraint.assert_not_called()
        mock_drop_column.assert_not_called()


def test_c23_downgrade_succeeds_when_no_duplicates():
    """Verify c23 downgrade restores unique constraint and drops column when no duplicates exist."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    migration = script.get_revision("c80000000023").module

    mock_bind = MagicMock()
    mock_result = MagicMock()
    mock_result.fetchall.return_value = []
    mock_bind.execute.return_value = mock_result

    with patch.object(migration.op, "get_bind", return_value=mock_bind), \
         patch.object(migration.context, "is_offline_mode", return_value=False), \
         patch.object(migration.op, "drop_index") as mock_drop_index, \
         patch.object(migration.op, "create_unique_constraint") as mock_create_uq, \
         patch.object(migration.op, "drop_constraint") as mock_drop_constraint, \
         patch.object(migration.op, "drop_column") as mock_drop_column:

        migration.downgrade()

        # Drops delivery and legacy indexes, restores uq constraint, drops delivery_id col
        dropped_indexes = [c.args[0] for c in mock_drop_index.call_args_list]
        assert dropped_indexes == [
            "uq_post_metric_snapshots_delivery_bucket",
            "uq_post_metric_snapshots_legacy_job_bucket",
            "ix_post_metric_snapshots_delivery_id",
        ]
        mock_create_uq.assert_called_once_with(
            "uq_post_metric_snapshots_job_bucket",
            "post_metric_snapshots",
            ["publish_job_id", "bucket"],
        )
        mock_drop_constraint.assert_called_once_with(
            "fk_post_metric_snapshots_delivery_id",
            "post_metric_snapshots",
            type_="foreignkey",
        )
        mock_drop_column.assert_called_once_with("post_metric_snapshots", "delivery_id")
