import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.settings import Settings


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Settings.__table__.create(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    row = Settings(
        id=1,
        crawl_interval_minutes=30,
        max_post_age_days=2,
        ai_provider_primary="gemini",
        ai_provider_fallback="groq",
        ai_fallback_after_failures=3,
        ai_fallback_reset_after_minutes=15,
    )
    session.add(row)
    session.commit()
    yield session
    session.close()


@pytest.fixture
def client(test_db):
    app.dependency_overrides[get_db] = lambda: test_db
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)


def test_get_settings_prompt_polish_defaults(client):
    res = client.get("/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["prompt_polish_base_url"] == "http://172.17.0.1:8098"
    assert data["has_prompt_polish_token"] is False
    assert data["prompt_polish_model"] == "gemini-argon"
    # Token must never be returned
    assert "prompt_polish_token" not in data
    for key in data:
        assert not key.endswith("_encrypted"), f"encrypted key leaked: {key}"


def test_put_prompt_polish_token_encrypted(client, test_db):
    res = client.put("/settings", json={"prompt_polish_token": "abc123"})
    assert res.status_code == 200
    assert res.json()["has_prompt_polish_token"] is True
    assert "abc123" not in res.text

    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.prompt_polish_token_encrypted is not None
    assert row.prompt_polish_token_encrypted != "abc123"

    get_res = client.get("/settings")
    assert get_res.json()["has_prompt_polish_token"] is True


def test_put_prompt_polish_token_empty_clears(client, test_db):
    client.put("/settings", json={"prompt_polish_token": "secret99"})
    res = client.put("/settings", json={"prompt_polish_token": ""})
    assert res.status_code == 200
    assert res.json()["has_prompt_polish_token"] is False

    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.prompt_polish_token_encrypted is None


def test_put_prompt_polish_base_url_validation_and_strip(client, test_db):
    bad = client.put("/settings", json={"prompt_polish_base_url": "ftp://x"})
    assert bad.status_code == 400
    assert "prompt_polish_base_url must start with http:// or https://" in bad.json()["detail"]

    good = client.put("/settings", json={"prompt_polish_base_url": "http://h:8098/"})
    assert good.status_code == 200
    assert good.json()["prompt_polish_base_url"] == "http://h:8098"

    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.prompt_polish_base_url == "http://h:8098"


def test_put_prompt_polish_model_validation(client, test_db):
    bad = client.put("/settings", json={"prompt_polish_model": "bad model!"})
    assert bad.status_code == 422

    good = client.put("/settings", json={"prompt_polish_model": "gemini-argon"})
    assert good.status_code == 200
    assert good.json()["prompt_polish_model"] == "gemini-argon"
