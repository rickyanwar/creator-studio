import pytest
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import json

from app.models.api_tokens import ApiToken
from app.services import api_tokens
from app.models.target_fanpages import TargetFanpage
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.strategy import FanpageContentMemory
from app.services.content_memory import build_auto_memory, update_auto, update_notes, get_memory
from app.tasks.content_memory import refresh_content_memory
from app.api.deps import get_current_user
from app.main import app as main_app
from app.database import Base, engine, SessionLocal, get_db

import uuid

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

def _create_job_with_metrics(db: Session, fp_id: int, sched: datetime, title: str, likes: int):
    job = PublishJob(
        fanpage_id=fp_id,
        content_type="ig_repost",
        scheduled_for=sched,
        status=PublishJobStatus.published,
        design_title=title,
        is_deleted=False
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    
    snap = PostMetricSnapshot(
        publish_job_id=job.id,
        fanpage_id=fp_id,
        bucket="7d",
        captured_at=datetime.now(timezone.utc),
        age_minutes=10080,
        likes=likes,
        comments=0,
        shares=0
    )
    db.add(snap)
    db.commit()
    return job

def test_build_auto_memory(db_session: Session):
    fp = TargetFanpage(repliz_account_id=str(uuid.uuid4()), name="London Page", timezone="Europe/London", is_active=True)
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    
    # Needs >= 5 posts for best_hours to consider it
    now = datetime.now(timezone.utc)
    # Hour 15 UTC -> 15 or 16 in London
    for i in range(6):
        _create_job_with_metrics(db_session, fp.id, now - timedelta(days=i, hours=2), f"Top Post {i}", 100 + i*10)
    
    auto = build_auto_memory(db_session, fp)
    
    assert auto["timezone"] == "Europe/London"
    assert "computed_at" in auto
    assert "best_hours" in auto
    assert len(auto["best_hours"]) > 0
    assert "best_weekdays" in auto
    assert len(auto["top_posts"]) == 5
    assert auto["top_posts"][0]["title"] == "Top Post 5"  # Highest engagement (150)
    
    db_session.query(PostMetricSnapshot).delete()
    db_session.query(PublishJob).delete()
    db_session.delete(fp)
    db_session.commit()

def test_fanpage_without_metrics(db_session: Session):
    fp = TargetFanpage(repliz_account_id=str(uuid.uuid4()), name="Empty Page", timezone="UTC", is_active=True)
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    
    auto = build_auto_memory(db_session, fp)
    
    assert auto["timezone"] == "UTC"
    assert "computed_at" in auto
    assert "best_hours" not in auto
    assert "trend" not in auto
    
    db_session.delete(fp)
    db_session.commit()

def test_refresh_task(db_session: Session):
    fp_active = TargetFanpage(repliz_account_id=str(uuid.uuid4()), name="Active", timezone="UTC", is_active=True)
    fp_inactive = TargetFanpage(repliz_account_id=str(uuid.uuid4()), name="Inactive", timezone="UTC", is_active=False)
    db_session.add(fp_active)
    db_session.add(fp_inactive)
    db_session.commit()
    db_session.refresh(fp_active)
    db_session.refresh(fp_inactive)
    
    # Update notes for active
    update_notes(db_session, fp_active.id, {"a": "b"})
    
    fp_active_id = fp_active.id
    fp_inactive_id = fp_inactive.id
    
    import app.tasks.content_memory
    app.tasks.content_memory.get_db = lambda: iter([SessionLocal()])
    
    refresh_content_memory()
    
    mem_active = get_memory(db_session, fp_active_id)
    assert mem_active.auto is not None
    assert mem_active.notes == {"a": "b"}  # Notes preserved
    
    mem_inactive = get_memory(db_session, fp_inactive_id)
    assert mem_inactive is None
    
    db_session.query(FanpageContentMemory).delete()
    db_session.delete(fp_active)
    db_session.delete(fp_inactive)
    db_session.commit()

def test_notes_limit(db_session: Session):
    fp = TargetFanpage(repliz_account_id=str(uuid.uuid4()), name="Limit Page", timezone="UTC", is_active=True)
    db_session.add(fp)
    db_session.commit()
    
    big_notes = {"pad": "x" * 17000}
    with pytest.raises(ValueError):
        update_notes(db_session, fp.id, big_notes)
        
    db_session.delete(fp)
    db_session.commit()

def test_endpoints(db_session: Session):
    client = TestClient(main_app)
    main_app.dependency_overrides[get_current_user] = lambda: "admin"
    main_app.dependency_overrides[get_db] = lambda: db_session
    
    fp = TargetFanpage(repliz_account_id=str(uuid.uuid4()), name="Endpoint Page", timezone="UTC", is_active=True)
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    
    # GET admin - empty
    resp = client.get(f"/strategy/memory/{fp.id}")
    assert resp.status_code == 200
    assert resp.json()["auto"] is None
    
    # 404 admin
    resp = client.get(f"/strategy/memory/999999")
    assert resp.status_code == 404
    
    # POST admin refresh
    resp = client.post(f"/strategy/memory/{fp.id}/refresh")
    assert resp.status_code == 200
    assert resp.json()["auto"] is not None
    
    token_read, plain_read = api_tokens.create_token(db_session, "Read", ["read"])
    token_write, plain_write = api_tokens.create_token(db_session, "Write", ["memory:write"])
    
    # GET hermes
    resp = client.get(f"/hermes/memory/{fp.id}", headers={"Authorization": f"Bearer {plain_read}"})
    assert resp.status_code == 200
    
    # PUT hermes with read token -> 403
    resp = client.put(f"/hermes/memory/{fp.id}", json={"notes": {"hello": "world"}}, headers={"Authorization": f"Bearer {plain_read}"})
    assert resp.status_code == 403
    
    # PUT hermes with write token -> 200
    resp = client.put(f"/hermes/memory/{fp.id}", json={"notes": {"hello": "world"}}, headers={"Authorization": f"Bearer {plain_write}"})
    assert resp.status_code == 200
    assert resp.json()["notes"]["hello"] == "world"
    assert resp.json()["auto"] is not None # Auto preserved
    
    db_session.query(FanpageContentMemory).delete()
    db_session.delete(fp)
    db_session.delete(token_read)
    db_session.delete(token_write)
    db_session.commit()
    main_app.dependency_overrides.clear()
