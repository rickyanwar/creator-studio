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


def test_get_settings_pro_poster_defaults(client):
    res = client.get("/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["pro_poster_enabled"] is False
    assert data["pro_poster_fanpage_ids"] == []


def test_patch_settings_pro_poster_round_trip(client, test_db):
    res = client.patch(
        "/settings",
        json={"pro_poster_enabled": True, "pro_poster_fanpage_ids": [10, 20]},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["pro_poster_enabled"] is True
    assert data["pro_poster_fanpage_ids"] == [10, 20]

    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.pro_poster_enabled is True
    assert row.pro_poster_fanpage_ids == [10, 20]

    get_res = client.get("/settings")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["pro_poster_enabled"] is True
    assert get_data["pro_poster_fanpage_ids"] == [10, 20]


def test_patch_pro_poster_fanpage_ids_dedup_and_sort(client, test_db):
    res = client.patch(
        "/settings",
        json={"pro_poster_fanpage_ids": [42, 5, 42, 1]},
    )
    assert res.status_code == 200
    assert res.json()["pro_poster_fanpage_ids"] == [1, 5, 42]

    test_db.expire_all()
    row = test_db.query(Settings).filter_by(id=1).first()
    assert row.pro_poster_fanpage_ids == [1, 5, 42]


def test_patch_pro_poster_non_int_fanpage_id_rejected(client):
    res = client.patch("/settings", json={"pro_poster_fanpage_ids": ["abc"]})
    assert res.status_code in (400, 422)


def test_put_settings_pro_poster(client, test_db):
    res = client.put(
        "/settings",
        json={"pro_poster_enabled": True, "pro_poster_fanpage_ids": [3]},
    )
    assert res.status_code == 200
    assert res.json()["pro_poster_enabled"] is True
    assert res.json()["pro_poster_fanpage_ids"] == [3]
