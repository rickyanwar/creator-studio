import json
import logging
import sqlite3
from unittest.mock import MagicMock, patch
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.settings import Settings
from app.models.social_publishing import (
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.services.phone_runner_client import PhoneRunnerClient
from app.tasks.phone_watchdog import check_phone_devices


@pytest.fixture
def db_session():
    """Create isolated in-memory SQLite database with ck_operational_notifications_state intact."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    DeviceLease.__table__.create(engine)
    OperationalNotification.__table__.create(engine)
    Settings.__table__.create(engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        yield session
    finally:
        session.close()
        sd_col.server_default = orig_sd


def _seed_device_and_dest(db, *, serial: str = "127.0.0.1:5555", name: str = "test-phone"):
    fanpage = TargetFanpage(name="Test Page", is_active=True)
    db.add(fanpage)
    db.commit()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name="Test Dest",
        mode="phone",
        is_active=True,
    )
    db.add(dest)
    db.commit()

    device = PhoneDevice(name=name, adb_serial=serial, status="available")
    db.add(device)
    db.commit()

    assignment = DestinationDeviceAssignment(
        destination_id=dest.id,
        device_id=device.id,
        is_active=True,
    )
    db.add(assignment)
    db.commit()

    return device, dest


def test_operational_notification_states_allowed(db_session):
    """Verify 'pending', 'sent', 'failed', 'open', 'resolved' are accepted by constraint."""
    for idx, state in enumerate(["pending", "sent", "failed", "open", "resolved"]):
        notif = OperationalNotification(
            dedupe_key=f"test_state_{state}_{idx}",
            kind="test",
            message=f"msg_{state}",
            state=state,
        )
        db_session.add(notif)
    db_session.commit()

    rows = db_session.query(OperationalNotification).all()
    assert len(rows) == 5
    states = {r.state for r in rows}
    assert states == {"pending", "sent", "failed", "open", "resolved"}


def test_operational_notification_invalid_state_rejected(db_session):
    """Verify constraint rejects invalid states on SQLite."""
    notif = OperationalNotification(
        dedupe_key="test_invalid",
        kind="test",
        message="msg_invalid",
        state="invalid_status",
    )
    db_session.add(notif)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_watchdog_maintenance_path_writes_open_then_resolved_with_constraint(db_session):
    """Verify full phone_watchdog flow writing 'open' and 'resolved' succeeds with constraint active."""
    device, _ = _seed_device_and_dest(db_session)

    client = MagicMock(spec=PhoneRunnerClient)
    # Tick 1: device is unhealthy -> probe returns portal_ready False, recover fails
    client.probe_device.return_value = {
        "success": True,
        "status": "portal_unready",
        "adb_connected": True,
        "portal_ready": False,
    }
    client.send_notification.return_value = (True, "sent")

    # Tick 1 execution: device available -> maintenance, marker written with state='open'
    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "maintenance"

    marker = (
        db_session.query(OperationalNotification)
        .filter_by(dedupe_key=f"phone_watchdog:{device.id}")
        .first()
    )
    assert marker is not None
    assert marker.state == "open"

    # Tick 2: device becomes healthy again -> probe returns portal_ready True
    client.probe_device.return_value = {
        "success": True,
        "status": "ready",
        "adb_connected": True,
        "portal_ready": True,
    }

    # Tick 2 execution: healthy + open marker -> device status='available', marker.state='resolved'
    check_phone_devices(client=client, db=db_session)

    db_session.refresh(device)
    assert device.status == "available"

    db_session.refresh(marker)
    assert marker.state == "resolved"


def test_migration_c80000000034_metadata():
    """Verify migration lineage and revision details."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    heads = script.get_heads()
    assert len(heads) == 1
    assert "c80000000034" in {r.revision for r in script.walk_revisions()}

    rev34 = script.get_revision("c80000000034")
    assert rev34 is not None
    assert rev34.down_revision == "c80000000033"


def test_migration_c80000000034_downgrade_with_existing_rows(caplog):
    """Downgrade logs warning and preserves wide constraint when rows use open/resolved."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    mod34 = script.get_revision("c80000000034").module

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE operational_notifications ("
                "id INTEGER PRIMARY KEY, "
                "dedupe_key VARCHAR(128) UNIQUE NOT NULL, "
                "kind VARCHAR(64) NOT NULL, "
                "message TEXT NOT NULL, "
                "state VARCHAR(32) NOT NULL, "
                "CONSTRAINT ck_operational_notifications_state CHECK ("
                "state IN ('pending', 'sent', 'failed', 'open', 'resolved')))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO operational_notifications (id, dedupe_key, kind, message, state) "
                "VALUES (1, 'k1', 'w', 'm', 'open')"
            )
        )
        conn.commit()

        with patch.object(mod34.op, "get_bind", return_value=conn):
            with caplog.at_level(logging.WARNING):
                mod34.downgrade()
            assert "Leaving wide ck_operational_notifications_state constraint" in caplog.text

            conn.execute(
                text(
                    "INSERT INTO operational_notifications (id, dedupe_key, kind, message, state) "
                    "VALUES (2, 'k2', 'w', 'm', 'resolved')"
                )
            )
            conn.commit()


def test_migration_c80000000034_downgrade_without_open_resolved_rows():
    """Downgrade recreates old constraint when no open/resolved rows exist."""
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    mod34 = script.get_revision("c80000000034").module

    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.execute(
            text(
                "CREATE TABLE operational_notifications ("
                "id INTEGER PRIMARY KEY, "
                "dedupe_key VARCHAR(128) UNIQUE NOT NULL, "
                "kind VARCHAR(64) NOT NULL, "
                "message TEXT NOT NULL, "
                "state VARCHAR(32) NOT NULL, "
                "CONSTRAINT ck_operational_notifications_state CHECK ("
                "state IN ('pending', 'sent', 'failed', 'open', 'resolved')))"
            )
        )
        conn.execute(
            text(
                "INSERT INTO operational_notifications (id, dedupe_key, kind, message, state) "
                "VALUES (1, 'k1', 'w', 'm', 'sent')"
            )
        )
        conn.commit()

        # Mock batch_alter_table context manager to track constraint recreation
        mock_batch = MagicMock()
        mock_ctx = MagicMock()
        mock_ctx.__enter__.return_value = mock_batch
        mock_ctx.__exit__.return_value = False

        with patch.object(mod34.op, "get_bind", return_value=conn), \
             patch.object(mod34.op, "batch_alter_table", return_value=mock_ctx):
            mod34.downgrade()

            mock_batch.drop_constraint.assert_called_once_with(
                "ck_operational_notifications_state", type_="check"
            )
            mock_batch.create_check_constraint.assert_called_once_with(
                "ck_operational_notifications_state",
                "state IN ('pending', 'sent', 'failed')",
            )

