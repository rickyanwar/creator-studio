from datetime import datetime, timezone
from unittest.mock import Mock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    DeviceLease,
    OperationalNotification,
    PhoneDevice,
    PublishDelivery,
    RecoveryIncident,
    SocialDestination,
)
from app.services.social_delivery_dispatch import (
    _recovering_phone_destinations_query,
    dispatch_due_deliveries,
    replan_recovering_phone_deliveries,
)


@pytest.fixture
def db_session():
    orig_visit_array = getattr(SQLiteTypeCompiler, "visit_ARRAY", None)
    orig_visit_jsonb = getattr(SQLiteTypeCompiler, "visit_JSONB", None)
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
    SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = None
    session = None
    try:
        engine = create_engine("sqlite:///:memory:")
        TargetFanpage.__table__.create(engine)
        SocialDestination.__table__.create(engine)
        PhoneDevice.__table__.create(engine)
        DestinationDeviceAssignment.__table__.create(engine)
        DeviceLease.__table__.create(engine)
        RecoveryIncident.__table__.create(engine)
        OperationalNotification.__table__.create(engine)
        PublishJob.__table__.create(engine)
        PublishDelivery.__table__.create(engine)
        DeliveryEvent.__table__.create(engine)

        Session = sessionmaker(bind=engine)
        session = Session()
        yield session
    finally:
        if session is not None:
            session.close()
        if engine is not None:
            engine.dispose()
        sd_col.server_default = orig_sd
        de_col.server_default = orig_de
        if orig_visit_array is not None:
            SQLiteTypeCompiler.visit_ARRAY = orig_visit_array
        elif hasattr(SQLiteTypeCompiler, "visit_ARRAY"):
            delattr(SQLiteTypeCompiler, "visit_ARRAY")
        if orig_visit_jsonb is not None:
            SQLiteTypeCompiler.visit_JSONB = orig_visit_jsonb
        elif hasattr(SQLiteTypeCompiler, "visit_JSONB"):
            delattr(SQLiteTypeCompiler, "visit_JSONB")


def _create_fixture_project(db, name="Project 1"):
    fanpage = TargetFanpage(name=name, repliz_account_id=f"rep_{name}", is_active=True)
    db.add(fanpage)
    db.flush()
    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_profile",
        name=f"{name} Dest",
        facebook_actor_id=f"actor_{name}",
        mode="phone",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=1,
        repliz_daily_target=0,
        min_gap_minutes=15,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    db.add(dest)
    db.flush()
    return fanpage, dest


def _create_fixture_device(db, name, status="available", calibrated=True):
    dev = PhoneDevice(
        name=name,
        expected_profile_actor_id=f"actor_{name}",
        status=status,
        tailscale_ip="100.64.0.1",
        adb_serial=f"serial_{name}",
        calibration_verified_at=datetime(2026, 1, 1, 0, 0, 0) if calibrated else None,
    )
    db.add(dev)
    db.flush()
    return dev


def test_postgresql_dialect_compile_no_distinct_and_scalar_in(db_session):
    query = _recovering_phone_destinations_query(db_session, device_id=None)
    sql = str(query.statement.compile(dialect=postgresql.dialect()))

    assert "DISTINCT" not in sql
    assert "social_destinations.supported_media" in sql
    assert "social_destinations.id IN (SELECT social_destinations.id" in sql
    assert "social_destinations.is_active IS true" in sql
    assert "destination_device_assignments.is_active IS true" in sql
    assert "phone_devices.status = %(status_1)s" in sql
    assert "phone_devices.calibration_verified_at IS NOT NULL" in sql
    assert "phone_devices.id = %" not in sql


def test_postgresql_dialect_compile_with_device_id(db_session):
    query = _recovering_phone_destinations_query(db_session, device_id=42)
    sql = str(query.statement.compile(dialect=postgresql.dialect()))

    assert "DISTINCT" not in sql
    assert "phone_devices.id = %(id_1)s" in sql


def test_sqlite_multiple_eligible_devices_single_destination_dedup(db_session):
    fanpage, dest = _create_fixture_project(db_session, name="MultiDev")
    dev1 = _create_fixture_device(db_session, "Phone_A", status="available", calibrated=True)
    dev2 = _create_fixture_device(db_session, "Phone_B", status="available", calibrated=True)

    db_session.add(DestinationDeviceAssignment(destination_id=dest.id, device_id=dev1.id, priority=0, is_active=True))
    db_session.add(DestinationDeviceAssignment(destination_id=dest.id, device_id=dev2.id, priority=1, is_active=True))
    db_session.commit()

    matching_dests = _recovering_phone_destinations_query(db_session).all()
    assert len(matching_dests) == 1
    assert matching_dests[0].id == dest.id

    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    for i in range(2):
        job = PublishJob(
            fanpage_id=fanpage.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://cdn.example.com/{i}.png",
            ai_generated_caption=f"News {i}",
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="waiting_for_device",
            intended_method="phone",
            caption_snapshot=f"News {i}",
            media_kind="image",
        )
        db_session.add(deliv)
    db_session.commit()

    replanned = replan_recovering_phone_deliveries(db_session, now_utc=now)
    assert len(replanned) == 2
    assert all(d.state == "scheduled" for d in replanned)
    dates = [d.scheduled_for.date() for d in replanned]
    assert len(set(dates)) == 2


def test_sqlite_replan_excludes_inactive_unavailable_uncalibrated(db_session):
    fanpage, dest = _create_fixture_project(db_session, name="Filtered")

    # Uncalibrated device
    dev_uncal = _create_fixture_device(db_session, "Uncal", status="available", calibrated=False)
    db_session.add(DestinationDeviceAssignment(destination_id=dest.id, device_id=dev_uncal.id, priority=0, is_active=True))

    # Maintenance device
    dev_maint = _create_fixture_device(db_session, "Maint", status="maintenance", calibrated=True)
    db_session.add(DestinationDeviceAssignment(destination_id=dest.id, device_id=dev_maint.id, priority=1, is_active=True))

    # Available + calibrated, but inactive assignment
    dev_inact = _create_fixture_device(db_session, "InactAssign", status="available", calibrated=True)
    db_session.add(DestinationDeviceAssignment(destination_id=dest.id, device_id=dev_inact.id, priority=2, is_active=False))
    db_session.commit()

    assert _recovering_phone_destinations_query(db_session).all() == []
    assert replan_recovering_phone_deliveries(db_session) == []

    # Now make destination inactive
    dest.is_active = False
    dev_good = _create_fixture_device(db_session, "Good", status="available", calibrated=True)
    db_session.add(DestinationDeviceAssignment(destination_id=dest.id, device_id=dev_good.id, priority=3, is_active=True))
    db_session.commit()

    assert _recovering_phone_destinations_query(db_session).all() == []
    assert replan_recovering_phone_deliveries(db_session) == []


def test_sqlite_replan_device_id_filtering_only_matching_destination(db_session):
    fanpage1, dest1 = _create_fixture_project(db_session, name="Proj1")
    dev1 = _create_fixture_device(db_session, "Phone_1", status="available", calibrated=True)
    db_session.add(DestinationDeviceAssignment(destination_id=dest1.id, device_id=dev1.id, priority=0, is_active=True))

    fanpage2, dest2 = _create_fixture_project(db_session, name="Proj2")
    dev2 = _create_fixture_device(db_session, "Phone_2", status="available", calibrated=True)
    db_session.add(DestinationDeviceAssignment(destination_id=dest2.id, device_id=dev2.id, priority=0, is_active=True))
    db_session.commit()

    # Query with device_id=dev1.id returns ONLY dest1
    matching_dev1 = _recovering_phone_destinations_query(db_session, device_id=dev1.id).all()
    assert [d.id for d in matching_dev1] == [dest1.id]

    # Query with device_id=dev2.id returns ONLY dest2
    matching_dev2 = _recovering_phone_destinations_query(db_session, device_id=dev2.id).all()
    assert [d.id for d in matching_dev2] == [dest2.id]

    # Query with non-existent device returns empty
    assert _recovering_phone_destinations_query(db_session, device_id=9999).all() == []

    # Add waiting delivery for both destinations
    now = datetime(2026, 10, 15, 10, 0, 0, tzinfo=timezone.utc)
    for dest, fp, tag in [(dest1, fanpage1, "Job1"), (dest2, fanpage2, "Job2")]:
        job = PublishJob(
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            design_image_url=f"https://cdn.example.com/{tag}.png",
            ai_generated_caption=tag,
            status=PublishJobStatus.pending_publish,
        )
        db_session.add(job)
        db_session.flush()
        deliv = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="waiting_for_device",
            intended_method="phone",
            caption_snapshot=tag,
            media_kind="image",
        )
        db_session.add(deliv)
    db_session.commit()

    # Replan specifically for dev1.id: only dest1's delivery is replanned
    replanned = replan_recovering_phone_deliveries(db_session, device_id=dev1.id, now_utc=now)
    assert len(replanned) == 1
    assert replanned[0].destination_id == dest1.id
    assert replanned[0].state == "scheduled"

    # Verify dest2 delivery remained in waiting_for_device
    dest2_deliv = db_session.query(PublishDelivery).filter_by(destination_id=dest2.id).one()
    assert dest2_deliv.state == "waiting_for_device"


def test_empty_dataset_dispatch_queries_no_error(db_session):
    assert replan_recovering_phone_deliveries(db_session) == []
    dispatched = dispatch_due_deliveries(
        db_session,
        runner_client=Mock(),
        repliz_client=Mock(),
    )
    assert dispatched == []
