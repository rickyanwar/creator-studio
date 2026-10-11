from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
import pytest

from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_current_user, get_db
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.target_fanpages import TargetFanpage
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.settings import Settings
from app.services.analytics import (
    JobRow,
    JobSnapshot,
    get_local_tz,
    calc_trend,
    compute_by_hour,
    compute_daily,
    compute_by_content_type,
    compute_top_posts
)
from app.database import Base, engine, SessionLocal


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_pure_local_hour_bucketing_london_switch():
    tz = ZoneInfo("Europe/London")
    j1 = JobRow(
        id=1, fanpage_id=1, content_type="ig_repost",
        scheduled_for=datetime(2026, 10, 24, 18, 30, tzinfo=timezone.utc),
        title="t1", snapshots={"7d": JobSnapshot("7d", 1, 1, 1)}
    )
    j2 = JobRow(
        id=2, fanpage_id=1, content_type="ig_repost",
        scheduled_for=datetime(2026, 10, 26, 18, 30, tzinfo=timezone.utc),
        title="t2", snapshots={"7d": JobSnapshot("7d", 1, 1, 1)}
    )
    
    res = compute_by_hour([j1, j2], tz)
    
    hour_19 = next(x for x in res if x["hour"] == 19)
    assert hour_19["posts"] == 1
    assert hour_19["eng_median"] is None
    
    hour_18 = next(x for x in res if x["hour"] == 18)
    assert hour_18["posts"] == 1
    assert hour_18["eng_median"] is None


def test_pure_n_less_than_5():
    tz = ZoneInfo("UTC")
    jobs = []
    for i in range(4):
        jobs.append(JobRow(
            id=i, fanpage_id=1, content_type="ig_repost",
            scheduled_for=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            title="t", snapshots={"7d": JobSnapshot("7d", 1, 0, 0)}
        ))
    
    res = compute_by_hour(jobs, tz)
    hour_12 = next(x for x in res if x["hour"] == 12)
    assert hour_12["posts"] == 4
    assert hour_12["eng_median"] is None
    
    jobs.append(JobRow(
        id=4, fanpage_id=1, content_type="ig_repost",
        scheduled_for=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        title="t", snapshots={"7d": JobSnapshot("7d", 1, 0, 0)}
    ))
    res = compute_by_hour(jobs, tz)
    hour_12 = next(x for x in res if x["hour"] == 12)
    assert hour_12["posts"] == 5
    assert hour_12["eng_median"] == 1


def test_pure_trend_labels():
    now = datetime(2026, 1, 30, tzinfo=timezone.utc)
    
    def _create_jobs(start_day, count, eng):
        res = []
        for i in range(count):
            res.append(JobRow(
                id=i, fanpage_id=1, content_type="ig_repost",
                scheduled_for=datetime(2026, 1, start_day, tzinfo=timezone.utc),
                title="t", snapshots={"24h": JobSnapshot("24h", eng, 0, 0)}
            ))
        return res
        
    assert calc_trend(_create_jobs(20, 9, 10) + _create_jobs(5, 10, 10), now)["label"] == "insufficient"
    
    t_rising = calc_trend(_create_jobs(20, 10, 12) + _create_jobs(5, 10, 10), now)
    assert t_rising["label"] == "rising"
    assert t_rising["change_pct"] == 20.0
    
    t_falling = calc_trend(_create_jobs(20, 10, 8) + _create_jobs(5, 10, 10), now)
    assert t_falling["label"] == "falling"
    assert t_falling["change_pct"] == -20.0
    
    t_flat = calc_trend(_create_jobs(20, 10, 11) + _create_jobs(5, 10, 10), now)
    assert t_flat["label"] == "flat"
    assert t_flat["change_pct"] == 10.0


def test_pure_daily_zero_fill():
    tz = ZoneInfo("UTC")
    jobs = [
        JobRow(
            id=1, fanpage_id=1, content_type="ig_repost",
            scheduled_for=datetime(2026, 1, 5, tzinfo=timezone.utc),
            title="t", snapshots={"7d": JobSnapshot("7d", 1, 0, 0)}
        )
    ]
    
    since = datetime(2026, 1, 3, tzinfo=timezone.utc)
    until = datetime(2026, 1, 7, tzinfo=timezone.utc)
    
    res = compute_daily(jobs, tz, since, until)
    assert len(res) == 5
    assert res[0]["date"] == "2026-01-03"
    assert res[0]["posts"] == 0
    assert res[2]["date"] == "2026-01-05"
    assert res[2]["posts"] == 1


def test_api_analytics(db_session):
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    # Also override get_db to use our test db
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    
    setrow = db_session.query(Settings).first()
    old_metrics_enabled = None
    if not setrow:
        setrow = Settings(id=1, metrics_ingestion_enabled=True)
        db_session.add(setrow)
    else:
        old_metrics_enabled = setrow.metrics_ingestion_enabled
        setrow.metrics_ingestion_enabled = True
    db_session.commit()
    
    fp = TargetFanpage(
        repliz_account_id="r1_test_analytics", name="fan1", timezone="Europe/London", target_country="GB", is_active=True
    )
    db_session.add(fp)
    db_session.commit()
    
    now = datetime.now(timezone.utc)
    
    j = PublishJob(
        fanpage_id=fp.id, content_type=ContentType.ig_repost,
        scheduled_for=now - timedelta(days=2),
        status=PublishJobStatus.published, is_deleted=False,
        design_title="test job",
        ai_generated_caption="caption"
    )
    db_session.add(j)
    db_session.commit()
    
    s = PostMetricSnapshot(
        publish_job_id=j.id, fanpage_id=fp.id,
        bucket="7d", captured_at=now, age_minutes=10080,
        likes=10, comments=2, shares=1
    )
    db_session.add(s)
    db_session.commit()
    
    app.dependency_overrides.pop(get_current_user)
    assert client.get("/analytics/overview").status_code in (401, 403)
    
    app.dependency_overrides[get_current_user] = lambda: {"id": 1}
    assert client.get("/analytics/overview?days=6").status_code == 422
    assert client.get("/analytics/overview?days=181").status_code == 422
    
    from sqlalchemy import func
    max_id = db_session.query(func.max(TargetFanpage.id)).scalar() or 0
    assert client.get(f"/analytics/fanpages/{max_id + 1000}").status_code == 404
    
    resp = client.get("/analytics/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["days"] == 28
    assert "metrics" in data
    assert data["metrics"]["enabled"] is True
    assert data["metrics"]["snapshots_total"] >= 1
    assert "fanpages" in data
    
    fp_data = next((f for f in data["fanpages"] if f["fanpage_id"] == fp.id), None)
    assert fp_data is not None
    assert fp_data["name"] == "fan1"
    assert fp_data["posts"] == 1
    assert fp_data["eng_final_median"] is None # n<5
    
    resp2 = client.get(f"/analytics/fanpages/{fp.id}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["fanpage"]["name"] == "fan1"
    assert data2["totals"]["engagement"] == 13
    assert len(data2["by_hour"]) == 24
    assert len(data2["by_weekday"]) == 7
    
    db_session.delete(s)
    db_session.delete(j)
    db_session.delete(fp)
    if old_metrics_enabled is None:
        db_session.delete(setrow)
    else:
        setrow.metrics_ingestion_enabled = old_metrics_enabled
    db_session.commit()
    app.dependency_overrides.clear()
