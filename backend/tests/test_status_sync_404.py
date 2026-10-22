import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock
import httpx
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

# Allow SQLite to compile PostgreSQL ARRAY and JSONB types in memory
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"

from app.models.social_publishing import DeliveryEvent, PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.tasks.status_sync import _POLL_MIN_AGE, sync_pending_deliveries


def _now():
    return datetime(2026, 10, 22, 12, 0, 0)


@pytest.fixture
def delivery_db():
    """In-memory SQLite session for status sync 404 tests."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    session = Session(engine)
    dest = SocialDestination(
        id=1,
        content_project_id=1,
        kind="facebook_page",
        name="Sync Test Dest",
        mode="repliz",
        daily_target=1,
        phone_daily_target=0,
        repliz_daily_target=1,
        is_active=True,
    )
    session.add(dest)
    session.commit()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_repliz_404_marks_delivery_skipped_with_event(delivery_db, monkeypatch):
    """Repliz 404 HTTPStatusError marks delivery as skipped and records DeliveryEvent."""
    now = _now()
    deliv = PublishDelivery(
        publish_job_id=1,
        destination_id=1,
        state="repliz_scheduled",
        repliz_schedule_id="sched_404",
        scheduled_for=now - timedelta(minutes=15),
    )
    delivery_db.add(deliv)
    delivery_db.commit()

    resp = httpx.Response(404, request=httpx.Request("GET", "https://api.repliz.com/schedule/sched_404"))
    mock_client = MagicMock()
    mock_client.get_schedule.side_effect = httpx.HTTPStatusError("Not Found", request=resp.request, response=resp)
    monkeypatch.setattr("app.tasks.status_sync.get_repliz_client_from_db", lambda db: mock_client)

    synced = sync_pending_deliveries(db=delivery_db, now=now)
    assert synced == 1

    delivery_db.expire_all()
    reloaded = delivery_db.query(PublishDelivery).filter_by(repliz_schedule_id="sched_404").first()
    assert reloaded.state == "skipped"
    assert reloaded.last_error == "Repliz schedule not found (404) — deleted in Repliz"

    events = delivery_db.query(DeliveryEvent).filter_by(delivery_id=reloaded.id).all()
    assert len(events) == 1
    assert events[0].event_type == "skipped"
    assert events[0].details == {
        "error": "Repliz schedule not found (404) — deleted in Repliz",
        "source": "status_sync",
    }


def test_repliz_500_leaves_delivery_unchanged(delivery_db, monkeypatch):
    """Other HTTP errors (e.g. 500) keep existing warning behaviour and leave state unchanged."""
    now = _now()
    deliv = PublishDelivery(
        publish_job_id=2,
        destination_id=1,
        state="repliz_scheduled",
        repliz_schedule_id="sched_500",
        scheduled_for=now - timedelta(minutes=15),
    )
    delivery_db.add(deliv)
    delivery_db.commit()

    resp = httpx.Response(500, request=httpx.Request("GET", "https://api.repliz.com/schedule/sched_500"))
    mock_client = MagicMock()
    mock_client.get_schedule.side_effect = httpx.HTTPStatusError("Server Error", request=resp.request, response=resp)
    monkeypatch.setattr("app.tasks.status_sync.get_repliz_client_from_db", lambda db: mock_client)

    synced = sync_pending_deliveries(db=delivery_db, now=now)
    assert synced == 0

    delivery_db.expire_all()
    reloaded = delivery_db.query(PublishDelivery).filter_by(repliz_schedule_id="sched_500").first()
    assert reloaded.state == "repliz_scheduled"
    assert reloaded.last_error is None

    events = delivery_db.query(DeliveryEvent).filter_by(delivery_id=reloaded.id).all()
    assert len(events) == 0


def test_stale_5_day_old_repliz_404_marked_skipped(delivery_db, monkeypatch):
    """A 5-day-old repliz_scheduled row (> _POLL_MAX_AGE) with 404 is polled and marked skipped."""
    now = _now()
    deliv = PublishDelivery(
        publish_job_id=3,
        destination_id=1,
        state="repliz_scheduled",
        repliz_schedule_id="sched_stale_404",
        scheduled_for=now - timedelta(days=5),
    )
    delivery_db.add(deliv)
    delivery_db.commit()

    resp = httpx.Response(404, request=httpx.Request("GET", "https://api.repliz.com/schedule/sched_stale_404"))
    mock_client = MagicMock()
    mock_client.get_schedule.side_effect = httpx.HTTPStatusError("Not Found", request=resp.request, response=resp)
    monkeypatch.setattr("app.tasks.status_sync.get_repliz_client_from_db", lambda db: mock_client)

    synced = sync_pending_deliveries(db=delivery_db, now=now)
    assert synced == 1

    delivery_db.expire_all()
    reloaded = delivery_db.query(PublishDelivery).filter_by(repliz_schedule_id="sched_stale_404").first()
    assert reloaded.state == "skipped"
    assert reloaded.last_error == "Repliz schedule not found (404) — deleted in Repliz"

    events = delivery_db.query(DeliveryEvent).filter_by(delivery_id=reloaded.id).all()
    assert len(events) == 1
    assert events[0].event_type == "skipped"


def test_stale_5_day_old_repliz_published_marked_published(delivery_db, monkeypatch):
    """A 5-day-old repliz_scheduled row (> _POLL_MAX_AGE) whose Repliz status is published is marked published."""
    now = _now()
    deliv = PublishDelivery(
        publish_job_id=4,
        destination_id=1,
        state="repliz_scheduled",
        repliz_schedule_id="sched_stale_published",
        scheduled_for=now - timedelta(days=5),
    )
    delivery_db.add(deliv)
    delivery_db.commit()

    mock_client = MagicMock()
    mock_client.get_schedule.return_value = {
        "status": "published",
        "postId": "9876543210_1234567890",
        "url": "https://facebook.com/9876543210/posts/1234567890",
    }
    monkeypatch.setattr("app.tasks.status_sync.get_repliz_client_from_db", lambda db: mock_client)

    synced = sync_pending_deliveries(db=delivery_db, now=now)
    assert synced == 1

    delivery_db.expire_all()
    reloaded = delivery_db.query(PublishDelivery).filter_by(repliz_schedule_id="sched_stale_published").first()
    assert reloaded.state == "published"
    assert reloaded.facebook_post_id == "9876543210_1234567890"
    assert reloaded.facebook_permalink == "https://facebook.com/9876543210/posts/1234567890"

    events = delivery_db.query(DeliveryEvent).filter_by(delivery_id=reloaded.id).all()
    assert len(events) == 1
    assert events[0].event_type == "published"
