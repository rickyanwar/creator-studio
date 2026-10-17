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


def test_get_settings_gptcli_defaults(client):
    res = client.get("/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["gptcli_enabled"] is False
    assert data["gptcli_size"] == "1080x1350"
    assert data["ai_day_start"] == "05:30"
    assert data["codex_share_percent"] == 35
    assert data["has_gptcli_token"] is False
    assert data["gptcli_fanpage_ids"] == []


def test_put_gptcli_token_encryption(client, test_db):
    res = client.put("/settings", json={"gptcli_token": "abc"})
    assert res.status_code == 200
    assert res.json()["has_gptcli_token"] is True
    assert "abc" not in res.text
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.gptcli_token_encrypted is not None
    assert row.gptcli_token_encrypted != "abc"


def test_put_gptcli_token_empty_clears(client, test_db):
    client.put("/settings", json={"gptcli_token": "secret123"})
    res = client.put("/settings", json={"gptcli_token": ""})
    assert res.status_code == 200
    assert res.json()["has_gptcli_token"] is False
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.gptcli_token_encrypted is None


def test_put_gptcli_base_url_validation_and_strip(client, test_db):
    bad = client.put("/settings", json={"gptcli_base_url": "ftp://x"})
    assert bad.status_code == 400
    assert "gptcli_base_url must start with http:// or https://" in bad.json()["detail"]

    good = client.put("/settings", json={"gptcli_base_url": "http://172.17.0.1:8099/"})
    assert good.status_code == 200
    assert good.json()["gptcli_base_url"] == "http://172.17.0.1:8099"
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.gptcli_base_url == "http://172.17.0.1:8099"


def test_put_validation_errors(client):
    res1 = client.put("/settings", json={"codex_share_percent": 101})
    assert res1.status_code == 422

    res2 = client.put("/settings", json={"ai_day_start": "25:00"})
    assert res2.status_code == 422

    res3 = client.put("/settings", json={"gptcli_size": "big"})
    assert res3.status_code == 422


def test_put_gptcli_fanpage_ids_dedup_and_sort(client, test_db):
    res = client.put("/settings", json={"gptcli_fanpage_ids": [4, 4, 2]})
    assert res.status_code == 200
    assert res.json()["gptcli_fanpage_ids"] == [2, 4]
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.gptcli_fanpage_ids == [2, 4]


def test_put_visual_engine_daily_max_round_trips(client, test_db):
    res = client.put("/settings", json={"visual_engine_daily_max": 170})
    assert res.status_code == 200
    assert res.json()["visual_engine_daily_max"] == 170
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.visual_engine_daily_max == 170
    out = client.get("/settings").json()
    assert out["visual_engine_daily_max"] == 170


def test_put_codex_usage_url(client, test_db):
    bad = client.put("/settings", json={"codex_usage_url": "ftp://bad"})
    assert bad.status_code == 400

    empty = client.put("/settings", json={"codex_usage_url": ""})
    assert empty.status_code == 200
    assert empty.json()["codex_usage_url"] is None
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.codex_usage_url is None

    good = client.put("/settings", json={"codex_usage_url": "https://codex.example.com/usage"})
    assert good.status_code == 200
    assert good.json()["codex_usage_url"] == "https://codex.example.com/usage"
    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.codex_usage_url == "https://codex.example.com/usage"
