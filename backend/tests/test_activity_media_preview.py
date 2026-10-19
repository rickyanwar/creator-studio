"""Tests for TASK UI2: Scheduled delivery activity media preview fields (media_urls, thumbnail_url)."""

from datetime import datetime
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.models.users import User


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
    text("'[\"image\", \"album\", \"video\"]'")
)
DeliveryEvent.__table__.columns["details"].server_default = DefaultClause(
    text("'{}'")
)


@pytest.fixture
def db_session():
    """Create isolated SQLite database session with required tables."""
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
    """FastAPI TestClient with overridden DB and mock authenticated user."""
    mock_user = User(id=1, username="editor", password_hash="hash", is_active=True)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: mock_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_activity_image_media_preview(client, db_session):
    project = TargetFanpage(name="P1", timezone="UTC")
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page 1",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        min_gap_minutes=5,
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.pending_publish)
    db_session.add(job)
    db_session.flush()

    img_urls = [
        "https://autostudio.pro/media/designs/job_1_a.png",
        "https://autostudio.pro/media/designs/job_1_b.png",
    ]
    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        media_kind="image",
        media_snapshot=img_urls,
        scheduled_for=datetime(2026, 10, 20, 10, 0),
    )
    db_session.add(delivery)
    db_session.commit()

    resp = client.get("/social-media/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == delivery.id
    assert item["media_urls"] == img_urls
    assert item["thumbnail_url"] == "https://autostudio.pro/media/designs/job_1_a.png"


def test_activity_video_media_preview(client, db_session):
    project = TargetFanpage(name="P2", timezone="UTC")
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page 2",
        mode="phone",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=1,
        min_gap_minutes=5,
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    thumb_url = "https://autostudio.pro/media/thumbs/video_1_thumb.jpg"
    job = PublishJob(
        fanpage_id=project.id,
        status=PublishJobStatus.pending_publish,
        video_thumbnail_url=thumb_url,
    )
    db_session.add(job)
    db_session.flush()

    video_urls = ["https://autostudio.pro/media/videos/clip_1.mp4"]
    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="waiting_for_slot",
        media_kind="video",
        media_snapshot=video_urls,
        scheduled_for=datetime(2026, 10, 20, 11, 0),
    )
    db_session.add(delivery)
    db_session.commit()

    resp = client.get("/social-media/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == delivery.id
    assert item["media_urls"] == video_urls
    assert item["thumbnail_url"] == thumb_url


def test_activity_bad_media_snapshot_and_none(client, db_session):
    project = TargetFanpage(name="P3", timezone="UTC")
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page 3",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        min_gap_minutes=5,
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job1 = PublishJob(fanpage_id=project.id, status=PublishJobStatus.pending_publish)
    job2 = PublishJob(fanpage_id=project.id, status=PublishJobStatus.pending_publish)
    db_session.add_all([job1, job2])
    db_session.flush()

    # Delivery with dict snapshot
    d_dict = PublishDelivery(
        publish_job_id=job1.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        media_kind="image",
        media_snapshot={"error": "corrupted"},
    )
    # Delivery with None snapshot
    d_none = PublishDelivery(
        publish_job_id=job2.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        media_kind="image",
        media_snapshot=None,
    )
    db_session.add_all([d_dict, d_none])
    db_session.commit()

    resp = client.get("/social-media/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    for item in data:
        assert item["media_urls"] == []
        assert item["thumbnail_url"] is None


def test_activity_album_media_preview(client, db_session):
    project = TargetFanpage(name="P4", timezone="UTC")
    db_session.add(project)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Page 4",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        min_gap_minutes=5,
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.pending_publish)
    db_session.add(job)
    db_session.flush()

    album_urls = [
        "https://autostudio.pro/media/designs/album_1.png",
        "https://autostudio.pro/media/designs/album_2.png",
        "https://autostudio.pro/media/designs/album_3.png",
    ]
    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state="repliz_scheduled",
        media_kind="album",
        media_snapshot=album_urls,
        scheduled_for=datetime(2026, 10, 20, 12, 0),
    )
    db_session.add(delivery)
    db_session.commit()

    resp = client.get("/social-media/activity")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    assert item["id"] == delivery.id
    assert item["media_urls"] == album_urls
    assert item["thumbnail_url"] == "https://autostudio.pro/media/designs/album_1.png"

