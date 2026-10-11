import pytest
from datetime import datetime, timezone, timedelta
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models.api_tokens import ApiToken
from app.services import api_tokens
from app.api.deps import require_hermes_scope, get_current_user, _bearer
from app.main import app as main_app
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

def test_hashing_and_creation(db_session: Session):
    token_db, plaintext = api_tokens.create_token(db_session, "Test Token", ["read"])
    
    assert plaintext.startswith("hst_")
    assert token_db.token_hash != plaintext
    assert token_db.token_hash == api_tokens.hash_token(plaintext)
    
    # Cleanup
    db_session.delete(token_db)
    db_session.commit()

def test_verify_token(db_session: Session):
    token_db, plaintext = api_tokens.create_token(db_session, "Verify Token", ["read"])
    
    # Verify OK
    verified = api_tokens.verify(db_session, plaintext)
    assert verified is not None
    assert verified.id == token_db.id
    
    # Unknown
    assert api_tokens.verify(db_session, "hst_unknown123") is None
    
    # Revoked
    token_db.revoked_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.commit()
    assert api_tokens.verify(db_session, plaintext) is None
    
    # Cleanup
    db_session.delete(token_db)
    db_session.commit()

def test_require_hermes_scope(db_session: Session):
    # Setup test app
    test_app = FastAPI()
    
    @test_app.get("/test-scope")
    def test_scope_endpoint(token: ApiToken = Depends(require_hermes_scope("read"))):
        return {"token_id": token.id}
    
    client = TestClient(test_app)
    
    # Create tokens
    token_read, plain_read = api_tokens.create_token(db_session, "Read Token", ["read"])
    token_write, plain_write = api_tokens.create_token(db_session, "Write Token", ["memory:write"])
    
    # Provide a mock for get_db dependency since require_hermes_scope needs it
    from app.database import get_db
    test_app.dependency_overrides[get_db] = lambda: db_session
    
    # Missing token -> 403 (FastAPI HTTPBearer default)
    resp = client.get("/test-scope")
    assert resp.status_code == 403
    
    # Bad token -> 401
    resp = client.get("/test-scope", headers={"Authorization": "Bearer hst_bad"})
    assert resp.status_code == 401
    assert "Invalid or revoked token" in resp.json()["detail"]
    
    # Good token, wrong scope -> 403
    resp = client.get("/test-scope", headers={"Authorization": f"Bearer {plain_write}"})
    assert resp.status_code == 403
    assert "Missing required scope" in resp.json()["detail"]
    
    # Good token, correct scope -> 200
    assert token_read.last_used_at is None
    resp = client.get("/test-scope", headers={"Authorization": f"Bearer {plain_read}"})
    assert resp.status_code == 200
    assert resp.json()["token_id"] == token_read.id
    
    # Check last_used_at is set
    db_session.refresh(token_read)
    assert token_read.last_used_at is not None
    
    # Cleanup
    db_session.delete(token_read)
    db_session.delete(token_write)
    db_session.commit()

def test_token_endpoints(db_session: Session):
    client = TestClient(main_app)
    main_app.dependency_overrides[get_current_user] = lambda: "admin"
    from app.database import get_db
    main_app.dependency_overrides[get_db] = lambda: db_session
    
    # 1. Create Token
    resp = client.post("/strategy/tokens", json={"name": "Endpoint Token", "scopes": ["read"]})
    assert resp.status_code == 200
    data = resp.json()
    assert "token" in data
    plaintext = data["token"]
    assert plaintext.startswith("hst_")
    assert data["name"] == "Endpoint Token"
    assert data["scopes"] == ["read"]
    token_id = data["id"]
    
    # 2. Invalid scope -> 422
    resp_invalid = client.post("/strategy/tokens", json={"name": "Invalid", "scopes": ["bad:scope"]})
    assert resp_invalid.status_code == 422
    
    # 3. List Tokens
    resp = client.get("/strategy/tokens")
    assert resp.status_code == 200
    list_data = resp.json()
    
    # Find our created token
    listed_token = next((t for t in list_data if t["id"] == token_id), None)
    assert listed_token is not None
    assert "token" not in listed_token  # plaintext not in list
    assert "token_hash" not in listed_token # hash not in list
    assert listed_token["name"] == "Endpoint Token"
    
    # 4. Revoke Token
    resp = client.delete(f"/strategy/tokens/{token_id}")
    assert resp.status_code == 200
    
    # Check revoked_at in DB
    token_db = db_session.query(ApiToken).filter(ApiToken.id == token_id).first()
    assert token_db.revoked_at is not None
    
    # Cleanup
    db_session.delete(token_db)
    db_session.commit()
    main_app.dependency_overrides.clear()
