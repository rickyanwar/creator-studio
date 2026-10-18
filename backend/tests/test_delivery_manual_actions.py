"""Tests for delivery copies listing and manual actions (TASK C1)."""

from datetime import datetime, timezone
import json
from unittest import mock
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

    assignment = DestinationDeviceAssignment(
        id=50,
        destination_id=11,
        device_id=100,
        priority=1,
        is_active=True,
    )
    db_session.add(assignment)

    job1 = PublishJob(
        id=1,
        fanpage_id=1,
        status=PublishJobStatus.publishing,
        ai_generated_caption="Post 1",
    )
    job2 = PublishJob(
        id=2,
        fanpage_id=1,
        status=PublishJobStatus.failed,
        ai_generated_caption="Post 2",
    )
    job3 = PublishJob(
        id=3,
        fanpage_id=1,
        status=PublishJobStatus.published,
        ai_generated_caption="Post 3",
    )
    db_session.add_all([job1, job2, job3])
    db_session.commit()

    return fanpage, destination, destination_phone, device


# ==============================================================================
# 1. GET /social-media/deliveries
# ==============================================================================


def test_get_deliveries_empty(client):
    """Empty or missing publish_job_ids returns empty list."""
    res = client.get("/social-media/deliveries")
    assert res.status_code == 200
    assert res.json() == []

    res2 = client.get("/social-media/deliveries?publish_job_ids=")
    assert res2.status_code == 200
    assert res2.json() == []


def test_get_deliveries_filter_jobs(client, db_session):
    """Returns copies for requested jobs with all required fields and no leak from other jobs."""
    _, dest1, dest2, dev = _seed_base_data(db_session)

    # Deliveries for job 1
    d1 = PublishDelivery(
        id=101,
        publish_job_id=1,
        destination_id=dest1.id,
        state="uncertain",
        intended_method="repliz",
        uncertain_since=datetime(2026, 10, 18, 10, 0, 0),
        last_error="Repliz timeout",
    )
    d2 = PublishDelivery(
        id=102,
        publish_job_id=1,
        destination_id=dest2.id,
        device_id=dev.id,
        state="waiting_for_device",
        intended_method="phone",
        scheduled_for=datetime(2026, 10, 18, 12, 0, 0),
    )
    # Delivery for job 2
    d3 = PublishDelivery(
        id=103,
        publish_job_id=2,
        destination_id=dest1.id,
        state="failed",
        intended_method="repliz",
        last_error="Bad request",
    )
    # Delivery for job 3 (should NOT be returned if not in query)
    d4 = PublishDelivery(
        id=104,
        publish_job_id=3,
        destination_id=dest1.id,
        state="published",
        intended_method="repliz",
        published_at=datetime(2026, 10, 18, 9, 0, 0),
        repliz_schedule_id="rep-999",
    )
    db_session.add_all([d1, d2, d3, d4])
    db_session.commit()

    # Query job 1 and 2
    res = client.get("/social-media/deliveries?publish_job_ids=1,2")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 3
    ids = [x["id"] for x in data]
    assert ids == [101, 102, 103]

    item1 = data[0]
    assert item1["id"] == 101
    assert item1["publish_job_id"] == 1
    assert item1["destination_id"] == dest1.id
    assert item1["destination_name"] == "Formula Weekend Page"
    assert item1["destination_kind"] == "facebook_page"
    assert item1["platform"] == "facebook_page"
    assert item1["method"] == "repliz"
    assert item1["state"] == "uncertain"
    assert item1["last_error"] == "Repliz timeout"
    assert item1["uncertain_since"] is not None
    assert item1["repliz_schedule_id"] is None
    assert item1["facebook_post_id"] is None

    item2 = data[1]
    assert item2["id"] == 102
    assert item2["destination_name"] == "Formula Weekend Profile"
    assert item2["device_name"] == "Pixel 6a"
    assert item2["method"] == "phone"
    assert item2["state"] == "waiting_for_device"


def test_get_deliveries_validation(client):
    """Validation: invalid job ID returns 400, >100 job IDs returns 400."""
    res_bad = client.get("/social-media/deliveries?publish_job_ids=1,abc,3")
    assert res_bad.status_code == 400
    assert "Invalid job ID" in res_bad.json()["detail"]

    # 101 IDs
    many_ids = ",".join(str(i) for i in range(1, 102))
    res_many = client.get(f"/social-media/deliveries?publish_job_ids={many_ids}")
    assert res_many.status_code == 400
    assert "Maximum 100" in res_many.json()["detail"]


# ==============================================================================
# 2. POST /social-media/deliveries/{id}/mark-posted
# ==============================================================================


def test_mark_posted_from_uncertain(client, db_session):
    """Uncertain delivery copy marked posted transitions to published and records event."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=201,
        publish_job_id=1,
        destination_id=dest.id,
        state="uncertain",
        intended_method="repliz",
        uncertain_since=datetime(2026, 10, 18, 10, 0, 0),
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/201/mark-posted")
    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "published"
    assert data["published_at"] is not None
    assert data["uncertain_since"] is None

    # Check DB state
    db_session.expire_all()
    d_db = db_session.query(PublishDelivery).filter_by(id=201).first()
    assert d_db.state == "published"
    assert d_db.published_at is not None

    # Check DeliveryEvent
    events = db_session.query(DeliveryEvent).filter_by(delivery_id=201).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "manual_override"
    assert ev.details["actor"] == "owner"
    assert ev.details["note"] == "Marked posted by hand"
    assert ev.details["previous_state"] == "uncertain"
    assert ev.details["new_state"] == "published"

    # Parent PublishJob status updated to published
    job = db_session.query(PublishJob).filter_by(id=1).first()
    assert job.status == PublishJobStatus.published


@pytest.mark.parametrize("invalid_state", ["scheduled", "failed", "published", "waiting_for_slot"])
def test_mark_posted_refused_transitions(client, db_session, invalid_state):
    """mark-posted is refused (409) from any state other than uncertain."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=202,
        publish_job_id=1,
        destination_id=dest.id,
        state=invalid_state,
        intended_method="repliz",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/202/mark-posted")
    assert res.status_code == 409
    assert "only 'uncertain' deliveries can be marked posted" in res.json()["detail"]


# ==============================================================================
# 3. POST /social-media/deliveries/{id}/mark-not-posted
# ==============================================================================


def test_mark_not_posted_from_uncertain(client, db_session):
    """Uncertain delivery copy marked not posted transitions to failed and records event."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=301,
        publish_job_id=1,
        destination_id=dest.id,
        state="uncertain",
        intended_method="repliz",
        uncertain_since=datetime(2026, 10, 18, 10, 0, 0),
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/301/mark-not-posted")
    assert res.status_code == 200
    data = res.json()
    assert data["state"] == "failed"
    assert data["last_error"] == "Marked not posted by hand"
    assert data["uncertain_since"] is None

    # Check DB state
    db_session.expire_all()
    d_db = db_session.query(PublishDelivery).filter_by(id=301).first()
    assert d_db.state == "failed"
    assert d_db.last_error == "Marked not posted by hand"

    # Check DeliveryEvent
    events = db_session.query(DeliveryEvent).filter_by(delivery_id=301).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "manual_override"
    assert ev.details["actor"] == "owner"
    assert ev.details["note"] == "Marked not posted by hand"
    assert ev.details["previous_state"] == "uncertain"
    assert ev.details["new_state"] == "failed"


@pytest.mark.parametrize("invalid_state", ["scheduled", "failed", "published", "waiting_for_device"])
def test_mark_not_posted_refused_transitions(client, db_session, invalid_state):
    """mark-not-posted is refused (409) from any state other than uncertain."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=302,
        publish_job_id=1,
        destination_id=dest.id,
        state=invalid_state,
        intended_method="repliz",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/302/mark-not-posted")
    assert res.status_code == 409
    assert "only 'uncertain' deliveries can be marked not posted" in res.json()["detail"]


# ==============================================================================
# 4. POST /social-media/deliveries/{id}/retry
# ==============================================================================


def test_retry_delivery_success(client, db_session):
    """Failed delivery without external ID can be retried, reset counters, and enqueues dispatch."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=401,
        publish_job_id=2,  # job2 is failed
        destination_id=dest.id,
        state="failed",
        intended_method="repliz",
        attempt_count=3,
        last_error="Previous network failure",
        media_kind="image",
    )
    db_session.add(d)
    db_session.commit()

    with mock.patch("app.tasks.social_delivery.dispatch_social_deliveries.delay") as mock_delay:
        res = client.post("/social-media/deliveries/401/retry")
        assert res.status_code == 200
        data = res.json()
        assert data["state"] == "scheduled"
        assert data["attempt_count"] == 0
        assert data["scheduled_for"] is not None
        assert data["last_error"] is None

        # Assert Celery enqueue was called
        mock_delay.assert_called_once()

    # DB state verified
    db_session.expire_all()
    d_db = db_session.query(PublishDelivery).filter_by(id=401).first()
    assert d_db.state == "scheduled"
    assert d_db.attempt_count == 0
    assert d_db.last_error is None

    # DeliveryEvent recorded
    events = db_session.query(DeliveryEvent).filter_by(delivery_id=401).all()
    assert len(events) == 1
    ev = events[0]
    assert ev.event_type == "manual_override"
    assert ev.details["note"] == "Retried by hand"
    assert ev.details["previous_state"] == "failed"
    assert ev.details["new_state"] == "scheduled"

    # Parent job status reset from failed to pending_publish
    job = db_session.query(PublishJob).filter_by(id=2).first()
    assert job.status == PublishJobStatus.pending_publish


@pytest.mark.parametrize("invalid_state", ["uncertain", "scheduled", "published", "waiting_for_slot"])
def test_retry_refused_wrong_state(client, db_session, invalid_state):
    """retry is refused (409) if delivery is not in 'failed' state."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=402,
        publish_job_id=1,
        destination_id=dest.id,
        state=invalid_state,
        intended_method="repliz",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/402/retry")
    assert res.status_code == 409
    assert "only 'failed' copies can be retried" in res.json()["detail"]


def test_retry_refused_external_id_repliz(client, db_session):
    """retry is refused (409) if failed delivery has repliz_schedule_id."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=403,
        publish_job_id=2,
        destination_id=dest.id,
        state="failed",
        intended_method="repliz",
        repliz_schedule_id="repliz_sched_12345",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/403/retry")
    assert res.status_code == 409
    assert "repliz_sched_12345" in res.json()["detail"]


def test_retry_refused_external_id_facebook(client, db_session):
    """retry is refused (409) if failed delivery has facebook_post_id."""
    _, dest, _, _ = _seed_base_data(db_session)

    d = PublishDelivery(
        id=404,
        publish_job_id=2,
        destination_id=dest.id,
        state="failed",
        intended_method="phone",
        facebook_post_id="fb_post_99999",
    )
    db_session.add(d)
    db_session.commit()

    res = client.post("/social-media/deliveries/404/retry")
    assert res.status_code == 409
    assert "fb_post_99999" in res.json()["detail"]


def test_actions_404_not_found(client):
    """Endpoints return 404 when delivery ID does not exist."""
    assert client.post("/social-media/deliveries/99999/mark-posted").status_code == 404
    assert client.post("/social-media/deliveries/99999/mark-not-posted").status_code == 404
    assert client.post("/social-media/deliveries/99999/retry").status_code == 404
