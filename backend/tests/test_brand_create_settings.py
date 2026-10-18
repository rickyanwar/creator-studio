"""Tests for new brand creation settings (country, timezone, copy settings)."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
    text("'[\"image\", \"album\", \"video\"]'")
)


@pytest.fixture
def db_session():
    """Isolated SQLite database session for content project tests."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TargetFanpage.__table__.create(bind=engine)
    SocialDestination.__table__.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with dependency overrides."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_create_brand_with_all_settings(client, db_session):
    """Providing country, timezone, caption_language, and caption_tone saves all fields."""
    payload = {
        "name": "French GP Fanpage",
        "timezone": "Europe/Paris",
        "target_country": "FR",
        "caption_language": "fr",
        "caption_tone": "humorous",
    }
    resp = client.post("/content-projects", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["name"] == "French GP Fanpage"
    assert data["timezone"] == "Europe/Paris"
    assert data["target_country"] == "FR"
    assert data["caption_language"] == "fr"
    assert data["caption_tone"] == "humorous"

    brand = db_session.query(TargetFanpage).filter_by(id=data["id"]).first()
    assert brand is not None
    assert brand.name == "French GP Fanpage"
    assert brand.timezone == "Europe/Paris"
    assert brand.target_country == "FR"
    assert brand.caption_language == "fr"
    assert brand.caption_tone == "humorous"
    assert brand.mode2_caption_language == "fr"
    assert brand.mode2_caption_tone == "humorous"


def test_create_brand_omitted_settings_old_defaults(client, db_session):
    """Omitted optional settings fall back to existing defaults."""
    payload = {"name": "Default Brand"}
    resp = client.post("/content-projects", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["name"] == "Default Brand"
    assert data["timezone"] == "Europe/London"
    assert data["target_country"] == "GB"
    assert data["caption_language"] == "en"
    assert data["caption_tone"] == "engaging"

    brand = db_session.query(TargetFanpage).filter_by(id=data["id"]).first()
    assert brand is not None
    assert brand.timezone == "Europe/London"
    assert brand.target_country == "GB"
    assert brand.caption_language == "en"
    assert brand.caption_tone == "engaging"


def test_create_brand_explicit_none_country(client, db_session):
    """Explicitly passing null for target_country saves null (Global)."""
    payload = {
        "name": "Global Motorsport",
        "target_country": None,
    }
    resp = client.post("/content-projects", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()

    assert data["target_country"] is None

    brand = db_session.query(TargetFanpage).filter_by(id=data["id"]).first()
    assert brand is not None
    assert brand.target_country is None


def test_create_brand_bad_timezone_422(client):
    """Invalid IANA timezone returns 422 Unprocessable Entity."""
    payload = {
        "name": "Invalid TZ Brand",
        "timezone": "Atlantis/City",
    }
    resp = client.post("/content-projects", json=payload)
    assert resp.status_code == 422


def test_create_brand_bad_country_422(client):
    """Invalid country format returns 422 Unprocessable Entity."""
    payload = {
        "name": "Invalid Country Brand",
        "target_country": "GLOBAL",
    }
    resp = client.post("/content-projects", json=payload)
    assert resp.status_code == 422
