import uuid
import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import GflowAccount
from app.api.deps import get_current_user


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = lambda: "admin"
    client = TestClient(app)
    yield client
    app.dependency_overrides.pop(get_current_user, None)


@pytest.fixture
def account_factory(db_session):
    created_ids = []

    def _create(**kwargs):
        defaults = {
            "worker_id": f"w_{uuid.uuid4().hex[:8]}",
            "profile": f"prof_{uuid.uuid4().hex[:8]}",
            "enabled": True,
            "status": "ok",
        }
        defaults.update(kwargs)
        acc = GflowAccount(**defaults)
        db_session.add(acc)
        db_session.commit()
        db_session.refresh(acc)
        created_ids.append(acc.id)
        return acc

    yield _create

    if created_ids:
        db_session.query(GflowAccount).filter(GflowAccount.id.in_(created_ids)).delete(synchronize_session=False)
        db_session.commit()


def test_terms_accepted_transitions_needs_terms_to_ok(client, db_session, account_factory):
    acc = account_factory(status="needs_terms", last_error="Upload terms required")
    res = client.patch(f"/ai-video/accounts/{acc.id}", json={"terms_accepted": True})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["last_error"] is None

    db_session.expire_all()
    reloaded = db_session.query(GflowAccount).filter_by(id=acc.id).first()
    assert reloaded.status == "ok"
    assert reloaded.last_error is None


def test_terms_accepted_ignored_when_not_needs_terms(client, db_session, account_factory):
    acc = account_factory(status="auth_error", last_error="Token expired")
    res = client.patch(f"/ai-video/accounts/{acc.id}", json={"terms_accepted": True})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "auth_error"
    assert data["last_error"] == "Token expired"

    db_session.expire_all()
    reloaded = db_session.query(GflowAccount).filter_by(id=acc.id).first()
    assert reloaded.status == "auth_error"
    assert reloaded.last_error == "Token expired"


def test_patch_enabled_still_works(client, db_session, account_factory):
    acc = account_factory(status="needs_terms", enabled=True)
    res = client.patch(f"/ai-video/accounts/{acc.id}", json={"enabled": False})
    assert res.status_code == 200
    data = res.json()
    assert data["enabled"] is False

    db_session.expire_all()
    reloaded = db_session.query(GflowAccount).filter_by(id=acc.id).first()
    assert reloaded.enabled is False
