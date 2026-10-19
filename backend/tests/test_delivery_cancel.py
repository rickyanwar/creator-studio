"""Tests for canceling scheduled delivery copies (TASK AS1)."""

from datetime import datetime, timezone
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.api.social_media import router as social_media_router
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    DeviceLease,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage

app = FastAPI()
app.include_router(social_media_router)


# Compile PostgreSQL-specific column types for SQLite
@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


# SQLite-safe defaults
SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
    text("'[\"image\", \"album\", \"video\"]'")
)
DeliveryEvent.__table__.columns["details"].server_default = DefaultClause(
    text("'{}'")
)


@pytest.fixture
def db_session():
    """Create a clean isolated SQLite database session with required tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        PhoneDevice.__table__,
        SocialDestination.__table__,
        DestinationDeviceAssignment.__table__,
        PublishJob.__table__,
        PublishDelivery.__table__,
        DeliveryEvent.__table__,
        DeviceLease.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with DB override and authenticated user override."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_base_data(db_session):
    """Helper to seed fanpage, destination, device, assignment, and job."""
    fanpage = TargetFanpage(
        id=1,
        name="Formula Weekend",
        timezone="Europe/London",
        publish_sleep_start_hour=23,
        publish_sleep_end_hour=6,
    )
    db_session.add(fanpage)

    destination = SocialDestination(
        id=10,
        content_project_id=1,
        kind="facebook_page",
        name="Formula Weekend Page",
        mode="repliz",
        timezone="Europe/London",
        daily_target=4,
        min_gap_minutes=60,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    db_session.add(destination)

    destination_phone = SocialDestination(
        id=11,
        content_project_id=1,
        kind="facebook_profile",
        name="Formula Weekend Profile",
        mode="phone",
        timezone="Europe/London",
        daily_target=2,
        min_gap_minutes=60,
        supported_media=["image", "album", "video"],
        is_active=True,
    )
    db_session.add(destination_phone)

    device = PhoneDevice(
        id=100,
        name="Pixel 6a",
        status="available",
    )
    db_session.add(device)
    db_session.flush()

    job1 = PublishJob(
        id=1,
        fanpage_id=1,
        status=PublishJobStatus.pending_publish,
        ai_generated_caption="Post 1",
    )
    db_session.add(job1)
    db_session.commit()

    return fanpage, destination, destination_phone, device


def test_cancel_scheduled_delivery_success(client, db_session):
    """Cancel a scheduled delivery: returns 200, sets state skipped, records manual_override event."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=101,
        publish_job_id=1,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        scheduled_for=datetime(2026, 10, 19, 14, 0, 0),
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/101/cancel")
    assert res.status_code == 200
    data = res.json()
    assert data["id"] == 101
    assert data["state"] == "skipped"
    assert data["last_error"] == "Cancelled by hand before posting"

    # Verify DB state
    db_session.expire_all()
    d_db = db_session.query(PublishDelivery).filter_by(id=101).first()
    assert d_db.state == "skipped"
    assert d_db.last_error == "Cancelled by hand before posting"

    # Verify event
    events = db_session.query(DeliveryEvent).filter_by(delivery_id=101).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "manual_override"
    assert ev.details["actor"] == "owner"
    assert ev.details["note"] == "Cancelled before posting"
    assert ev.details["previous_state"] == "scheduled"
    assert ev.details["new_state"] == "skipped"


def test_cancel_job_becomes_skipped_when_only_delivery(client, db_session):
    """Job status transitions to skipped when its only delivery is cancelled."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=102,
        publish_job_id=1,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/102/cancel")
    assert res.status_code == 200

    db_session.expire_all()
    job = db_session.query(PublishJob).filter_by(id=1).first()
    assert job.status == PublishJobStatus.skipped


def test_cancel_job_not_skipped_when_another_delivery_scheduled(client, db_session):
    """Job status does NOT transition to skipped when another delivery is still active/scheduled."""
    _, dest, dest_phone, _ = _seed_base_data(db_session)

    d1 = PublishDelivery(
        id=103,
        publish_job_id=1,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
    )
    d2 = PublishDelivery(
        id=104,
        publish_job_id=1,
        destination_id=dest_phone.id,
        state="scheduled",
        intended_method="phone",
    )
    db_session.add_all([d1, d2])
    db_session.commit()

    res = client.post("/social-media/deliveries/103/cancel")
    assert res.status_code == 200

    db_session.expire_all()
    job = db_session.query(PublishJob).filter_by(id=1).first()
    assert job.status == PublishJobStatus.pending_publish


@pytest.mark.parametrize("invalid_state", ["publishing", "published", "repliz_scheduled", "uncertain", "failed", "skipped"])
def test_cancel_refused_invalid_states(client, db_session, invalid_state):
    """Cannot cancel a delivery in states other than scheduled/waiting_for_slot/waiting_for_device (409)."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=105,
        publish_job_id=1,
        destination_id=dest.id,
        state=invalid_state,
        intended_method="repliz",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/105/cancel")
    assert res.status_code == 409
    assert f"Cannot cancel a delivery in state '{invalid_state}'" in res.json()["detail"]


def test_cancel_refused_when_claimed_at_set(client, db_session):
    """Cannot cancel a delivery when claimed_at is set (409)."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=106,
        publish_job_id=1,
        destination_id=dest.id,
        state="scheduled",
        intended_method="repliz",
        claimed_at=datetime(2026, 10, 19, 14, 0, 0),
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/106/cancel")
    assert res.status_code == 409
    assert "claimed" in res.json()["detail"]


def test_cancel_refused_when_external_id_set(client, db_session):
    """Cannot cancel a delivery when facebook_post_id or repliz_schedule_id is set (409)."""
    _, dest, dest_phone, _ = _seed_base_data(db_session)

    d_fb = PublishDelivery(
        id=107,
        publish_job_id=1,
        destination_id=dest.id,
        state="scheduled",
        facebook_post_id="fb_post_123",
    )
    d_repliz = PublishDelivery(
        id=108,
        publish_job_id=1,
        destination_id=dest_phone.id,
        state="scheduled",
        repliz_schedule_id="repliz_sched_456",
    )
    db_session.add_all([d_fb, d_repliz])
    db_session.commit()

    res1 = client.post("/social-media/deliveries/107/cancel")
    assert res1.status_code == 409
    assert "external post ID" in res1.json()["detail"]

    res2 = client.post("/social-media/deliveries/108/cancel")
    assert res2.status_code == 409
    assert "external post ID" in res2.json()["detail"]


@pytest.mark.parametrize("allowed_state", ["waiting_for_slot", "waiting_for_device"])
def test_cancel_allowed_waiting_states(client, db_session, allowed_state):
    """Allowed to cancel in waiting_for_slot and waiting_for_device states."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=109,
        publish_job_id=1,
        destination_id=dest.id,
        state=allowed_state,
        intended_method="repliz",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/109/cancel")
    assert res.status_code == 200
    assert res.json()["state"] == "skipped"


def test_cancel_delivery_not_found(client):
    """Returns 404 for unknown delivery ID."""
    res = client.post("/social-media/deliveries/99999/cancel")
    assert res.status_code == 404
    assert res.json()["detail"] == "Delivery copy not found"
