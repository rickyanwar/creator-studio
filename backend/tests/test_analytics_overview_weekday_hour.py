"""Tests for Analytics Overview by_weekday and by_hour aggregation across all brands."""

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.target_fanpages import TargetFanpage
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.settings import Settings


# Compile PostgreSQL-specific column types for SQLite
@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db_session():
    """Isolated in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        PublishJob.__table__,
        PostMetricSnapshot.__table__,
        Settings.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()

    settings = Settings(
        id=1,
        metrics_ingestion_enabled=True,
        metrics_plan_status="active",
        gptcli_enabled=False,
        gptcli_fanpage_ids=[],
    )
    session.add(settings)
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with dependency overrides."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: {"id": 1, "username": "admin"}
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_overview_empty_fanpages(client):
    """When no fanpages exist, overview returns empty by_hour (24 slots) and by_weekday (7 slots)."""
    resp = client.get("/analytics/overview?days=28")
    assert resp.status_code == 200
    data = resp.json()

    assert data["days"] == 28
    assert "metrics" in data
    assert "fanpages" in data
    assert data["fanpages"] == []

    assert "by_hour" in data
    assert len(data["by_hour"]) == 24
    for item in data["by_hour"]:
        assert item["posts"] == 0
        assert item["n"] == 0
        assert item["eng_median"] is None

    assert "by_weekday" in data
    assert len(data["by_weekday"]) == 7
    expected_labels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    for i, item in enumerate(data["by_weekday"]):
        assert item["weekday"] == i
        assert item["label"] == expected_labels[i]
        assert item["posts"] == 0
        assert item["n"] == 0
        assert item["eng_median"] is None


def test_overview_multi_brand_timezones(db_session, client):
    """Jobs across different brands are bucketed by each brand's local timezone."""
    base_now = datetime.now(timezone.utc)
    target_date = (base_now - timedelta(days=3)).date()

    # Brand 1: Asia/Jakarta (UTC+7)
    fp1 = TargetFanpage(id=1, name="Jakarta Brand", timezone="Asia/Jakarta", is_active=True)
    # Brand 2: Europe/London (UTC+1 in summer, UTC+0 in winter)
    fp2 = TargetFanpage(id=2, name="London Brand", timezone="Europe/London", is_active=True)
    db_session.add_all([fp1, fp2])
    db_session.flush()

    # Scheduled at 13:00 UTC 3 days ago:
    utc_dt1 = datetime(target_date.year, target_date.month, target_date.day, 13, 0, 0, tzinfo=timezone.utc)
    j1 = PublishJob(
        id=1,
        fanpage_id=fp1.id,
        status=PublishJobStatus.published,
        is_deleted=False,
        content_type=ContentType.ig_repost,
        scheduled_for=utc_dt1,
        ai_generated_caption="Jakarta post",
    )
    j2 = PublishJob(
        id=2,
        fanpage_id=fp2.id,
        status=PublishJobStatus.published,
        is_deleted=False,
        content_type=ContentType.ig_repost,
        scheduled_for=utc_dt1,
        ai_generated_caption="London post",
    )

    # Scheduled late at 23:45 UTC (may cross midnight in London if summer UTC+1):
    utc_dt2 = datetime(target_date.year, target_date.month, target_date.day, 23, 45, 0, tzinfo=timezone.utc)
    j3 = PublishJob(
        id=3,
        fanpage_id=fp2.id,
        status=PublishJobStatus.published,
        is_deleted=False,
        content_type=ContentType.ig_repost,
        scheduled_for=utc_dt2,
        ai_generated_caption="London late post",
    )

    db_session.add_all([j1, j2, j3])
    db_session.commit()

    resp = client.get("/analytics/overview?days=28")
    assert resp.status_code == 200
    data = resp.json()

    exp_jakarta_dt = utc_dt1.astimezone(ZoneInfo("Asia/Jakarta"))
    exp_london_dt1 = utc_dt1.astimezone(ZoneInfo("Europe/London"))
    exp_london_dt2 = utc_dt2.astimezone(ZoneInfo("Europe/London"))

    # Verify hour bucketing in brand local timezone
    hour_map = {item["hour"]: item for item in data["by_hour"]}
    assert hour_map[exp_jakarta_dt.hour]["posts"] >= 1
    assert hour_map[exp_london_dt1.hour]["posts"] >= 1
    assert hour_map[exp_london_dt2.hour]["posts"] >= 1
    assert sum(item["posts"] for item in data["by_hour"]) == 3

    # Verify weekday bucketing in brand local timezone
    weekday_map = {item["weekday"]: item for item in data["by_weekday"]}
    assert weekday_map[exp_jakarta_dt.weekday()]["posts"] >= 1
    assert weekday_map[exp_london_dt1.weekday()]["posts"] >= 1
    assert weekday_map[exp_london_dt2.weekday()]["posts"] >= 1
    assert sum(item["posts"] for item in data["by_weekday"]) == 3


def test_overview_median_calculation_threshold(db_session, client):
    """Median engagement is calculated when n >= 5 across brands, and is None when n < 5."""
    now = datetime.now(timezone.utc)
    target_dt = now - timedelta(days=2)

    fp1 = TargetFanpage(id=1, name="Brand A", timezone="UTC", is_active=True)
    fp2 = TargetFanpage(id=2, name="Brand B", timezone="UTC", is_active=True)
    db_session.add_all([fp1, fp2])
    db_session.flush()

    # Create 4 jobs at target_dt with metrics [10, 20, 30, 40]
    jobs = []
    snapshots = []
    for i, eng in enumerate([10, 20, 30, 40]):
        job = PublishJob(
            id=10 + i,
            fanpage_id=fp1.id if i % 2 == 0 else fp2.id,
            status=PublishJobStatus.published,
            is_deleted=False,
            content_type=ContentType.ig_repost,
            scheduled_for=target_dt,
            ai_generated_caption=f"Job {i}",
        )
        snap = PostMetricSnapshot(
            publish_job_id=job.id,
            fanpage_id=job.fanpage_id,
            bucket="7d",
            captured_at=now,
            age_minutes=10080,
            likes=eng,
            comments=0,
            shares=0,
        )
        jobs.append(job)
        snapshots.append(snap)

    db_session.add_all(jobs + snapshots)
    db_session.commit()

    resp = client.get("/analytics/overview?days=28")
    assert resp.status_code == 200
    data = resp.json()

    hour_item = next(item for item in data["by_hour"] if item["hour"] == target_dt.hour)
    assert hour_item["posts"] == 4
    assert hour_item["n"] == 4
    assert hour_item["eng_median"] is None  # n < 5

    # Now add a 5th job with metric 50 for fp2
    j5 = PublishJob(
        id=14,
        fanpage_id=fp2.id,
        status=PublishJobStatus.published,
        is_deleted=False,
        content_type=ContentType.ig_repost,
        scheduled_for=target_dt,
        ai_generated_caption="Job 5",
    )
    s5 = PostMetricSnapshot(
        publish_job_id=j5.id,
        fanpage_id=j5.fanpage_id,
        bucket="7d",
        captured_at=now,
        age_minutes=10080,
        likes=50,
        comments=0,
        shares=0,
    )
    db_session.add_all([j5, s5])
    db_session.commit()

    resp = client.get("/analytics/overview?days=28")
    assert resp.status_code == 200
    data = resp.json()

    hour_item = next(item for item in data["by_hour"] if item["hour"] == target_dt.hour)
    assert hour_item["posts"] == 5
    assert hour_item["n"] == 5
    # median of [10, 20, 30, 40, 50] is 30
    assert hour_item["eng_median"] == 30

    weekday_item = next(item for item in data["by_weekday"] if item["weekday"] == target_dt.weekday())
    assert weekday_item["posts"] == 5
    assert weekday_item["n"] == 5
    assert weekday_item["eng_median"] == 30


def test_overview_window_filtering(db_session, client):
    """Jobs outside the days window are excluded from by_hour and by_weekday."""
    now = datetime.now(timezone.utc)
    fp = TargetFanpage(id=1, name="Brand 1", timezone="UTC", is_active=True)
    db_session.add(fp)
    db_session.flush()

    # Job within 7 days
    j_inside = PublishJob(
        id=20,
        fanpage_id=fp.id,
        status=PublishJobStatus.published,
        is_deleted=False,
        content_type=ContentType.ig_repost,
        scheduled_for=now - timedelta(days=2),
        ai_generated_caption="Recent",
    )
    # Job 20 days ago (outside 7-day window)
    j_outside = PublishJob(
        id=21,
        fanpage_id=fp.id,
        status=PublishJobStatus.published,
        is_deleted=False,
        content_type=ContentType.ig_repost,
        scheduled_for=now - timedelta(days=20),
        ai_generated_caption="Old",
    )
    db_session.add_all([j_inside, j_outside])
    db_session.commit()

    resp = client.get("/analytics/overview?days=7")
    assert resp.status_code == 200
    data = resp.json()

    total_by_hour = sum(item["posts"] for item in data["by_hour"])
    total_by_weekday = sum(item["posts"] for item in data["by_weekday"])
    assert total_by_hour == 1
    assert total_by_weekday == 1


def test_overview_matches_per_brand_schema(db_session, client):
    """Keys and types in by_hour and by_weekday must match the per-brand endpoint shape."""
    fp = TargetFanpage(id=1, name="Brand A", timezone="UTC", is_active=True)
    db_session.add(fp)
    db_session.commit()

    overview_resp = client.get("/analytics/overview?days=28")
    brand_resp = client.get(f"/analytics/fanpages/{fp.id}?days=28")

    assert overview_resp.status_code == 200
    assert brand_resp.status_code == 200

    overview_data = overview_resp.json()
    brand_data = brand_resp.json()

    assert set(overview_data["by_hour"][0].keys()) == set(brand_data["by_hour"][0].keys())
    assert set(overview_data["by_weekday"][0].keys()) == set(brand_data["by_weekday"][0].keys())
