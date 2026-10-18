import pytest
from datetime import datetime, timezone, timedelta
import bcrypt
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.auth import REMEMBER_ME_DAYS
from app.api.deps import get_db
from app.config import get_settings
from app.main import app
from app.models.users import User

settings = get_settings()


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    User.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    pw_hash = bcrypt.hashpw(b"secret123", bcrypt.gensalt()).decode()
    user = User(username="testadmin", password_hash=pw_hash, is_active=True)
    session.add(user)
    session.commit()

    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_login_remember_me_30_days(client):
    res = client.post("/auth/login", json={"username": "testadmin", "password": "secret123", "remember": True})
    assert res.status_code == 200
    token = res.json()["access_token"]

    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "testadmin"

    expected_exp = datetime.now(timezone.utc) + timedelta(days=REMEMBER_ME_DAYS)
    # Expiration should match within 10 seconds
    assert abs(payload["exp"] - expected_exp.timestamp()) < 10


def test_login_default_expire_hours(client):
    res = client.post("/auth/login", json={"username": "testadmin", "password": "secret123", "remember": False})
    assert res.status_code == 200
    token = res.json()["access_token"]

    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "testadmin"

    expected_exp = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    assert abs(payload["exp"] - expected_exp.timestamp()) < 10


def test_login_omitted_remember_defaults_to_false(client):
    res = client.post("/auth/login", json={"username": "testadmin", "password": "secret123"})
    assert res.status_code == 200
    token = res.json()["access_token"]

    payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
    assert payload["sub"] == "testadmin"

    expected_exp = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    assert abs(payload["exp"] - expected_exp.timestamp()) < 10


def test_login_wrong_password_returns_401(client):
    res = client.post("/auth/login", json={"username": "testadmin", "password": "wrongpassword", "remember": True})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid credentials"


def test_login_nonexistent_user_returns_401(client):
    res = client.post("/auth/login", json={"username": "nouser", "password": "secret123"})
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid credentials"
