import pytest
from datetime import datetime, timezone
from app.services.strategy import validate_proposal, create_recommendation, approve, reject, LimitExceededError, ConflictError
from app.models.strategy import StrategyRecommendation
from app.models.target_fanpages import TargetFanpage
from app.models.api_tokens import ApiToken
from app.services import api_tokens
from sqlalchemy.orm import Session

def test_validate_proposal_sleep_window():
    # valid
    validate_proposal("sleep_window", {"publish_sleep_start_hour": 22, "publish_sleep_end_hour": 8})
    validate_proposal("sleep_window", {"publish_sleep_start_hour": None, "publish_sleep_end_hour": None})
    
    # invalid
    with pytest.raises(ValueError):
        validate_proposal("sleep_window", {"publish_sleep_start_hour": 22})
    with pytest.raises(ValueError):
        validate_proposal("sleep_window", {"publish_sleep_start_hour": 22, "publish_sleep_end_hour": None})
    with pytest.raises(ValueError):
        validate_proposal("sleep_window", {"publish_sleep_start_hour": -1, "publish_sleep_end_hour": 8})
    with pytest.raises(ValueError):
        validate_proposal("sleep_window", {"publish_sleep_start_hour": 24, "publish_sleep_end_hour": 8})
    with pytest.raises(ValueError):
        validate_proposal("sleep_window", {"publish_sleep_start_hour": 10, "publish_sleep_end_hour": 10})

def test_validate_proposal_daily_cap():
    # valid
    validate_proposal("daily_cap", {"publish_daily_limit": 50})
    validate_proposal("daily_cap", {"publish_daily_limit": 1})
    validate_proposal("daily_cap", {"publish_daily_limit": 100})
    
    # invalid
    with pytest.raises(ValueError):
        validate_proposal("daily_cap", {"publish_daily_limit": 0})
    with pytest.raises(ValueError):
        validate_proposal("daily_cap", {"publish_daily_limit": 101})
    with pytest.raises(ValueError):
        validate_proposal("daily_cap", {"limit": 50})

def test_validate_proposal_info_kinds():
    # valid
    validate_proposal("best_hours", {"any": "thing"})
    validate_proposal("topic", {})
    
    # invalid (oversized)
    with pytest.raises(ValueError):
        validate_proposal("content_mix", {"big": "a" * 4100})
        
    with pytest.raises(ValueError):
        validate_proposal("unknown", {})

@pytest.fixture(scope="module")
def setup_db():
    from app.database import Base, engine
    Base.metadata.create_all(bind=engine)
    yield

@pytest.fixture
def db_session(setup_db):
    from app.database import SessionLocal
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()

@pytest.fixture
def fanpage(db_session: Session):
    fp = TargetFanpage(
        repliz_account_id="test_fp_rec_123",
        name="Test FP Rec",
        publish_daily_limit=10,
        publish_sleep_start_hour=1,
        publish_sleep_end_hour=2
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    yield fp
    db_session.delete(fp)
    db_session.commit()

def test_admin_endpoints(db_session: Session, fanpage: TargetFanpage, hermes_token_read: str):
    from app.api.deps import get_current_user
    
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin"
    client = TestClient(app)
    
    # Create a proposal to test
    rec1 = create_recommendation(
        db_session, fanpage.id, "sleep_window", "Title", 
        {"publish_sleep_start_hour": 22, "publish_sleep_end_hour": 6}, 
        "rat", None, None
    )
    
    # GET recommendations (filter by fanpage_id, current_values present)
    res = client.get(f"/strategy/recommendations?fanpage_id={fanpage.id}")
    assert res.status_code == 200
    data = res.json()
    assert len(data) >= 1
    item = next(r for r in data if r["id"] == rec1.id)
    assert item["current_values"] is not None
    assert "publish_sleep_start_hour" in item["current_values"]
    
    # POST approve of a sleep_window proposal
    res = client.post(f"/strategy/recommendations/{rec1.id}/approve")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "applied"
    assert data["previous_values"] is not None
    assert data["decided_by"] == "admin"
    
    # Verify fanpage row really changed
    db_session.refresh(fanpage)
    assert fanpage.publish_sleep_start_hour == 22
    assert fanpage.publish_sleep_end_hour == 6
    
    # POST approve again -> 409
    res = client.post(f"/strategy/recommendations/{rec1.id}/approve")
    assert res.status_code == 409
    
    # POST reject of another proposal
    rec2 = create_recommendation(
        db_session, fanpage.id, "daily_cap", "Title2", 
        {"publish_daily_limit": 5}, 
        "rat", None, None
    )
    res = client.post(f"/strategy/recommendations/{rec2.id}/reject")
    assert res.status_code == 200
    assert res.json()["status"] == "rejected"
    
    # approve unknown id -> 404
    res = client.post("/strategy/recommendations/999999/approve")
    assert res.status_code == 404
    
    # approve with a Hermes token instead of the admin login -> 401/403
    app.dependency_overrides.pop(get_current_user, None)
    res = client.post(f"/strategy/recommendations/{rec2.id}/approve", headers={"Authorization": f"Bearer {hermes_token_read}"})
    assert res.status_code in (401, 403)
    
    db_session.delete(rec2)
    db_session.delete(rec1)
    db_session.commit()
    app.dependency_overrides.clear()

def test_supersede(db_session: Session, fanpage: TargetFanpage):
    r1 = create_recommendation(
        db_session, fanpage.id, "daily_cap", "Title 1", {"publish_daily_limit": 20}, "rat", None, None
    )
    assert r1.status == "proposed"
    
    r2 = create_recommendation(
        db_session, fanpage.id, "daily_cap", "Title 2", {"publish_daily_limit": 30}, "rat", None, None
    )
    
    db_session.refresh(r1)
    assert r1.status == "superseded"
    assert r2.status == "proposed"
    
    db_session.delete(r2)
    db_session.delete(r1)
    db_session.commit()

def test_429_over_limit(db_session: Session, fanpage: TargetFanpage):
    recs = []
    for i in range(20):
        # use info kind so it doesn't supersede
        rec = create_recommendation(db_session, fanpage.id, "other", f"Title {i}", {}, "rat", None, None)
        recs.append(rec)
        
    with pytest.raises(LimitExceededError):
        create_recommendation(db_session, fanpage.id, "other", "Title 21", {}, "rat", None, None)
        
    for rec in recs:
        db_session.delete(rec)
    db_session.commit()

def test_approve_applicable(db_session: Session, fanpage: TargetFanpage):
    rec = create_recommendation(
        db_session, fanpage.id, "sleep_window", "Change Sleep", 
        {"publish_sleep_start_hour": 23, "publish_sleep_end_hour": 6}, 
        "rat", None, None
    )
    
    approve(db_session, rec, "admin")
    
    db_session.refresh(fanpage)
    db_session.refresh(rec)
    
    assert rec.status == "applied"
    assert rec.previous_values == {"publish_sleep_start_hour": 1, "publish_sleep_end_hour": 2}
    assert fanpage.publish_sleep_start_hour == 23
    assert fanpage.publish_sleep_end_hour == 6
    
    db_session.delete(rec)
    db_session.commit()

def test_approve_info(db_session: Session, fanpage: TargetFanpage):
    rec = create_recommendation(
        db_session, fanpage.id, "content_mix", "Change Mix", {"mix": "more"}, "rat", None, None
    )
    approve(db_session, rec, "admin")
    assert rec.status == "approved"
    
    db_session.delete(rec)
    db_session.commit()

def test_409_approving_twice(db_session: Session, fanpage: TargetFanpage):
    rec = create_recommendation(
        db_session, fanpage.id, "other", "Title", {}, "rat", None, None
    )
    approve(db_session, rec, "admin")
    
    with pytest.raises(ConflictError):
        approve(db_session, rec, "admin")
        
    db_session.delete(rec)
    db_session.commit()

def test_reject(db_session: Session, fanpage: TargetFanpage):
    rec = create_recommendation(
        db_session, fanpage.id, "other", "Title", {}, "rat", None, None
    )
    reject(db_session, rec, "admin")
    
    assert rec.status == "rejected"
    
    db_session.delete(rec)
    db_session.commit()

@pytest.fixture
def hermes_token_read(db_session: Session):
    token, plaintext = api_tokens.create_token(db_session, "read_only", ["read"])
    yield plaintext
    db_session.delete(token)
    db_session.commit()

@pytest.fixture
def hermes_token_write(db_session: Session):
    token, plaintext = api_tokens.create_token(db_session, "write", ["recommendations:write", "read"])
    yield plaintext
    db_session.delete(token)
    db_session.commit()
    
from fastapi.testclient import TestClient
from app.main import app
from app.database import get_db

def test_hermes_endpoints(db_session: Session, fanpage: TargetFanpage, hermes_token_read: str, hermes_token_write: str):
    app.dependency_overrides[get_db] = lambda: db_session
    client = TestClient(app)
    # GET fanpages
    res = client.get("/hermes/fanpages", headers={"Authorization": f"Bearer {hermes_token_read}"})
    assert res.status_code == 200
    fps = res.json()
    assert any(fp["id"] == fanpage.id for fp in fps)
    
    # GET with missing token
    res = client.get("/hermes/fanpages")
    assert res.status_code == 403
    
    # POST recommendations with read token
    payload = {
        "fanpage_id": fanpage.id,
        "kind": "daily_cap",
        "title": "Title",
        "proposal": {"publish_daily_limit": 10},
        "rationale": "rat"
    }
    res = client.post("/hermes/recommendations", json=payload, headers={"Authorization": f"Bearer {hermes_token_read}"})
    assert res.status_code == 403
    
    # POST recommendations with write token
    res = client.post("/hermes/recommendations", json=payload, headers={"Authorization": f"Bearer {hermes_token_write}"})
    assert res.status_code == 201
    rec_id = res.json()["id"]
    
    # GET recommendations
    res = client.get(f"/hermes/recommendations?fanpage_id={fanpage.id}", headers={"Authorization": f"Bearer {hermes_token_read}"})
    assert res.status_code == 200
    assert any(r["id"] == rec_id for r in res.json())
    
    # 404 unknown fanpage
    payload["fanpage_id"] = 999999
    res = client.post("/hermes/recommendations", json=payload, headers={"Authorization": f"Bearer {hermes_token_write}"})
    assert res.status_code == 404
    
    # 422 invalid proposal (True as an hour or limit)
    payload["fanpage_id"] = fanpage.id
    payload["proposal"] = {"publish_daily_limit": True}
    res = client.post("/hermes/recommendations", json=payload, headers={"Authorization": f"Bearer {hermes_token_write}"})
    assert res.status_code == 422
    
    payload["kind"] = "sleep_window"
    payload["proposal"] = {"publish_sleep_start_hour": True, "publish_sleep_end_hour": 8}
    res = client.post("/hermes/recommendations", json=payload, headers={"Authorization": f"Bearer {hermes_token_write}"})
    assert res.status_code == 422
    
    # cleanup
    rec = db_session.get(StrategyRecommendation, rec_id)
    if rec:
        db_session.delete(rec)
        db_session.commit()
    app.dependency_overrides.clear()

