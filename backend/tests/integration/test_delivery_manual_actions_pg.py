"""Postgres integration tests for delivery manual actions (TASK G1).

Verifies that FOR UPDATE with joinedload (mark-posted, mark-not-posted, retry)
functions without SQL errors on real PostgreSQL.
"""

import os
from datetime import datetime, timezone
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")
if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL not set, skipping Postgres integration tests", allow_module_level=True)


@pytest.fixture
def client(db_session):
    """FastAPI test client with Postgres DB session and auth override."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _seed_delivery(db_session, state="uncertain", **kwargs):
    """Seed minimal fanpage, destination, publish_job, and delivery."""
    fanpage = TargetFanpage(
        name=f"PG Fanpage {datetime.now().timestamp()}",
        timezone="UTC",
    )
    db_session.add(fanpage)
    db_session.flush()

    dest = SocialDestination(
        content_project_id=fanpage.id,
        kind="facebook_page",
        name=f"PG Dest {datetime.now().timestamp()}",
        timezone="UTC",
        mode="repliz",
        repliz_account_id=f"rep_{int(datetime.now().timestamp() * 1000)}",
        is_active=True,
    )
    db_session.add(dest)
    db_session.flush()

    job = PublishJob(
        fanpage_id=fanpage.id,
        status=PublishJobStatus.publishing,
    )
    db_session.add(job)
    db_session.flush()

    delivery = PublishDelivery(
        publish_job_id=job.id,
        destination_id=dest.id,
        state=state,
        media_kind="image",
        **kwargs,
    )
    db_session.add(delivery)
    db_session.commit()
    db_session.refresh(delivery)
    return delivery


def test_pg_mark_delivery_posted(client, db_session):
    """Test mark-posted on real Postgres with FOR UPDATE OF publish_deliveries."""
    delivery = _seed_delivery(db_session, state="uncertain")

    res = client.post(f"/social-media/deliveries/{delivery.id}/mark-posted")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["id"] == delivery.id
    assert data["state"] == "published"
    assert data["published_at"] is not None

    db_session.refresh(delivery)
    assert delivery.state == "published"
    assert delivery.uncertain_since is None


def test_pg_mark_delivery_not_posted(client, db_session):
    """Test mark-not-posted on real Postgres with FOR UPDATE OF publish_deliveries."""
    delivery = _seed_delivery(db_session, state="uncertain")

    res = client.post(f"/social-media/deliveries/{delivery.id}/mark-not-posted")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["id"] == delivery.id
    assert data["state"] == "failed"
    assert data["last_error"] == "Marked not posted by hand"

    db_session.refresh(delivery)
    assert delivery.state == "failed"
    assert delivery.uncertain_since is None


def test_pg_retry_delivery_success(client, db_session):
    """Test retry on real Postgres for failed delivery without external ID."""
    delivery = _seed_delivery(db_session, state="failed")

    with patch("app.tasks.social_delivery.dispatch_social_deliveries.delay"):
        res = client.post(f"/social-media/deliveries/{delivery.id}/retry")
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["id"] == delivery.id
    assert data["state"] in ("scheduled", "waiting_for_slot", "waiting_for_device")

    db_session.refresh(delivery)
    assert delivery.state in ("scheduled", "waiting_for_slot", "waiting_for_device")


def test_pg_retry_guard_external_id(client, db_session):
    """Test retry-guard blocks retry when external ID exists."""
    delivery = _seed_delivery(
        db_session,
        state="failed",
        repliz_schedule_id="ext-sched-12345",
    )

    res = client.post(f"/social-media/deliveries/{delivery.id}/retry")
    assert res.status_code == 409, res.text
    assert "external ID" in res.json()["detail"]


def test_pg_retry_guard_wrong_state(client, db_session):
    """Test retry-guard blocks retry when state is not 'failed'."""
    delivery = _seed_delivery(db_session, state="uncertain")

    res = client.post(f"/social-media/deliveries/{delivery.id}/retry")
    assert res.status_code == 409, res.text
    assert "only 'failed' copies can be retried" in res.json()["detail"]
