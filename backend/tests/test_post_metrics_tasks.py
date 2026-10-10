import pytest
import uuid
import json
from datetime import datetime, timezone, timedelta
import httpx

from app.database import Base, engine, SessionLocal
from app.models.target_fanpages import TargetFanpage
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.tasks.post_metrics import collect_post_metrics, backfill_post_metrics
from app.services.repliz_client import ReplizPlanRequired
import app.tasks.post_metrics as pm_module

@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

@pytest.fixture
def db_session(setup_db):
    db = SessionLocal()
    s = db.query(Settings).filter_by(id=1).first()
    if not s:
        db.add(Settings(id=1, metrics_ingestion_enabled=True))
    else:
        s.metrics_ingestion_enabled = True
        s.metrics_plan_status = None
        s.metrics_plan_checked_at = None
        s.metrics_last_error = None
    db.commit()
    try:
        yield db
    finally:
        db.rollback()
        db.query(PostMetricSnapshot).delete()
        db.query(PublishJob).delete()
        db.query(TargetFanpage).filter(TargetFanpage.name.like("TestMetrics%")).delete()
        db.commit()
        db.close()

class FakeClient:
    def __init__(self, mode="ok"):
        self.mode = mode
        self.calls = []

    def get_content_statistic(self, content_id: str, account_id: str) -> dict:
        self.calls.append((content_id, account_id))
        if self.mode == "402":
            raise ReplizPlanRequired("Upgrade plan")
        elif self.mode == "429":
            resp = httpx.Response(429, request=httpx.Request("GET", "http://test"))
            raise httpx.HTTPStatusError("429", request=resp.request, response=resp)
        elif self.mode == "error":
            raise Exception("Generic")
        return {"data": {"like": 10, "comments": 2, "shares": 1}}

def freeze_time(monkeypatch, dt: datetime):
    class MockDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz == timezone.utc:
                return dt
            return super().now(tz)
    monkeypatch.setattr("app.tasks.post_metrics.datetime", MockDatetime)
    monkeypatch.setattr("app.tasks.post_metrics._CALL_PAUSE_SECONDS", 0)

def create_job(db_session, scheduled_for, post_id="123", status=PublishJobStatus.published, is_deleted=False):
    uid = uuid.uuid4().hex[:8]
    fanpage = TargetFanpage(
        repliz_account_id=f"test_{uid}",
        name=f"TestMetrics_{uid}",
        timezone="Europe/London"
    )
    db_session.add(fanpage)
    db_session.commit()
    
    j = PublishJob(
        fanpage_id=fanpage.id,
        scheduled_for=scheduled_for,
        status=status,
        is_deleted=is_deleted,
        repliz_response_json={"postId": post_id} if post_id else None
    )
    db_session.add(j)
    db_session.commit()
    return j

def test_flag_off(db_session, monkeypatch):
    s = db_session.query(Settings).first()
    s.metrics_ingestion_enabled = False
    db_session.commit()
    
    client = FakeClient()
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)
    
    collect_post_metrics()
    assert len(client.calls) == 0

def test_plan_required_throttle(db_session, monkeypatch):
    s = db_session.query(Settings).first()
    s.metrics_ingestion_enabled = True
    s.metrics_plan_status = "plan_required"
    s.metrics_plan_checked_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    db_session.commit()

    client = FakeClient()
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)
    
    collect_post_metrics()
    assert len(client.calls) == 0

def test_collect_due_selection(db_session, monkeypatch):
    now = datetime(2026, 10, 20, 12, 0)
    freeze_time(monkeypatch, now.replace(tzinfo=timezone.utc))

    j_65 = create_job(db_session, now - timedelta(minutes=65), "10065")
    j_80 = create_job(db_session, now - timedelta(minutes=80), "10080")
    j_deleted = create_job(db_session, now - timedelta(minutes=60), "10060", is_deleted=True)
    j_pending = create_job(db_session, now - timedelta(minutes=60), "10061", status=PublishJobStatus.pending_publish)
    j_nopost = create_job(db_session, now - timedelta(minutes=60), None)
    j_taken = create_job(db_session, now - timedelta(minutes=60), "10062")

    db_session.add(PostMetricSnapshot(
        publish_job_id=j_taken.id,
        fanpage_id=j_taken.fanpage_id,
        bucket="1h",
        captured_at=now,
        age_minutes=60
    ))
    db_session.commit()
    
    client = FakeClient()
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)

    collect_post_metrics()
    
    assert len(client.calls) == 1
    assert client.calls[0][0] == "10065"

    snap = db_session.query(PostMetricSnapshot).filter_by(publish_job_id=j_65.id).first()
    assert snap.bucket == "1h"
    assert snap.age_minutes == 65
    assert snap.likes == 10
    assert snap.comments == 2
    assert snap.shares == 1
    assert snap.raw_json == {"data": {"like": 10, "comments": 2, "shares": 1}}
    
    s = db_session.query(Settings).first()
    assert s.metrics_plan_status == "ok"
    assert s.metrics_plan_checked_at == now
    assert s.metrics_last_error is None

def test_402_plan_required(db_session, monkeypatch):
    now = datetime(2026, 10, 20, 12, 0)
    freeze_time(monkeypatch, now.replace(tzinfo=timezone.utc))

    s = db_session.query(Settings).first()
    s.metrics_plan_status = "plan_required"
    s.metrics_plan_checked_at = now - timedelta(hours=7)
    db_session.commit()

    j = create_job(db_session, now - timedelta(minutes=60), "40200")
    
    client = FakeClient(mode="402")
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)

    collect_post_metrics()
    
    assert len(client.calls) == 1
    
    db_session.refresh(s)
    assert s.metrics_plan_status == "plan_required"
    assert s.metrics_plan_checked_at == now

def test_429_stop(db_session, monkeypatch):
    now = datetime(2026, 10, 20, 12, 0)
    freeze_time(monkeypatch, now.replace(tzinfo=timezone.utc))

    s = db_session.query(Settings).first()
    s.metrics_plan_status = "ok"
    db_session.commit()

    j = create_job(db_session, now - timedelta(minutes=60), "42900")
    
    client = FakeClient(mode="429")
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)

    collect_post_metrics()
    
    db_session.refresh(s)
    assert s.metrics_plan_status == "ok"

def test_max_errors(db_session, monkeypatch):
    now = datetime(2026, 10, 20, 12, 0)
    freeze_time(monkeypatch, now.replace(tzinfo=timezone.utc))
    
    s = db_session.query(Settings).first()
    s.metrics_plan_status = "ok"
    db_session.commit()

    for i in range(15):
        create_job(db_session, now - timedelta(minutes=60), f"999{i:02d}")

    client = FakeClient(mode="error")
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)

    collect_post_metrics()
    
    assert len(client.calls) == pm_module._MAX_ERRORS + 1
    
    db_session.refresh(s)
    assert s.metrics_plan_status == "error"
    assert s.metrics_last_error == "Generic"

def test_backfill_selection(db_session, monkeypatch):
    now = datetime(2026, 10, 20, 12, 0)
    freeze_time(monkeypatch, now.replace(tzinfo=timezone.utc))
    monkeypatch.setattr(pm_module, "_BACKFILL_LIMIT", 2)
    
    s = db_session.query(Settings).first()
    s.metrics_plan_status = "ok"
    db_session.commit()

    j_in = create_job(db_session, now - timedelta(days=5), "10005")
    j_out1 = create_job(db_session, now - timedelta(days=9), "10009")
    j_out2 = create_job(db_session, now - timedelta(days=10), "10010")
    j_out3 = create_job(db_session, now - timedelta(days=12), "10012")
    
    j_out7d = create_job(db_session, now - timedelta(days=11), "10011")
    db_session.add(PostMetricSnapshot(
        publish_job_id=j_out7d.id, fanpage_id=j_out7d.fanpage_id,
        bucket="7d", captured_at=now, age_minutes=7*24*60
    ))
    db_session.commit()

    client = FakeClient()
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: client)

    backfill_post_metrics()
    
    assert len(client.calls) == 2
    assert client.calls[0][0] == "10009"
    assert client.calls[1][0] == "10010"

def test_duplicate_snapshot(db_session, monkeypatch):
    now = datetime(2026, 10, 20, 12, 0)
    freeze_time(monkeypatch, now.replace(tzinfo=timezone.utc))
    
    s = db_session.query(Settings).first()
    s.metrics_plan_status = "ok"
    db_session.commit()

    j = create_job(db_session, now - timedelta(minutes=60), "88888")

    class DupClient:
        def get_content_statistic(self, c, a):
            snap = PostMetricSnapshot(
                publish_job_id=j.id, fanpage_id=j.fanpage_id,
                bucket="1h", captured_at=now, age_minutes=60
            )
            with SessionLocal() as db2:
                db2.add(snap)
                db2.commit()
            return {"data": {"like": 10}}
            
    monkeypatch.setattr(pm_module, "get_repliz_client_from_db", lambda db: DupClient())
    
    collect_post_metrics()
    assert db_session.query(PostMetricSnapshot).count() == 1
