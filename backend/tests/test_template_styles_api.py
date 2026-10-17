"""Tests for template styles API, template default management, and fan page style integration."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.design_templates import DesignTemplate
from app.models.target_fanpages import TargetFanpage
from app.services.template_styles import STYLE_DEFAULTS


# Compile PostgreSQL-specific column types for SQLite
@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db_session():
    """Create a clean isolated SQLite database session with required tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        DesignTemplate.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI test client with DB override and authenticated user override."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_templates_styles(db_session, client):
    # GP templates with brandDefaults on news
    t_gp_news = DesignTemplate(
        name="GP News",
        style="gp",
        category="news",
        is_hidden=False,
        fanpage_id=None,
        template_json={"brandDefaults": {"primary": "#112233", "secondary": "#445566"}},
    )
    t_gp_quote = DesignTemplate(
        name="GP Quote",
        style="gp",
        category="quote",
        is_hidden=False,
        fanpage_id=None,
    )
    t_gp_disc = DesignTemplate(
        name="GP Discussion",
        style="gp",
        category="discussion",
        is_hidden=False,
        fanpage_id=None,
    )

    # Highlight templates without brandDefaults (fallback to STYLE_DEFAULTS["highlight"])
    t_hl_news = DesignTemplate(
        name="HL News",
        style="highlight",
        category="news",
        is_hidden=False,
        fanpage_id=None,
        template_json={},
    )
    t_hl_quote = DesignTemplate(
        name="HL Quote",
        style="highlight",
        category="quote",
        is_hidden=False,
        fanpage_id=None,
    )

    # Classic templates
    t_cl_news = DesignTemplate(
        name="CL News",
        style="classic",
        category="news",
        is_hidden=False,
        fanpage_id=None,
        template_json={"brandDefaults": {"primary": "#AABBCC"}},
    )

    db_session.add_all([t_gp_news, t_gp_quote, t_gp_disc, t_hl_news, t_hl_quote, t_cl_news])
    db_session.commit()

    resp = client.get("/templates/styles")
    assert resp.status_code == 200
    data = resp.json()

    assert len(data) == 3
    assert [d["style"] for d in data] == ["gp", "highlight", "classic"]
    assert [d["label"] for d in data] == ["GP Racing", "Highlight", "Classic"]

    # GP
    gp = data[0]
    assert gp["defaults"] == {"primary": "#112233", "secondary": "#445566"}
    assert gp["templates"]["news"] == {"id": t_gp_news.id, "name": "GP News"}
    assert gp["templates"]["quote"] == {"id": t_gp_quote.id, "name": "GP Quote"}
    assert gp["templates"]["discussion"] == {"id": t_gp_disc.id, "name": "GP Discussion"}

    # Highlight (fallback defaults)
    hl = data[1]
    assert hl["defaults"] == STYLE_DEFAULTS["highlight"]
    assert hl["templates"]["news"] == {"id": t_hl_news.id, "name": "HL News"}
    assert hl["templates"]["quote"] == {"id": t_hl_quote.id, "name": "HL Quote"}
    assert hl["templates"]["discussion"] is None

    # Classic
    cl = data[2]
    assert cl["defaults"] == {"primary": "#AABBCC"}
    assert cl["templates"]["news"] == {"id": t_cl_news.id, "name": "CL News"}
    assert cl["templates"]["quote"] is None
    assert cl["templates"]["discussion"] is None


def test_put_style_defaults(db_session, client):
    # 3 shared non-hidden highlight templates
    hl1 = DesignTemplate(name="HL News", style="highlight", category="news", is_hidden=False, fanpage_id=None, template_json={})
    hl2 = DesignTemplate(name="HL Quote", style="highlight", category="quote", is_hidden=False, fanpage_id=None, template_json={})
    hl3 = DesignTemplate(name="HL Disc", style="highlight", category="discussion", is_hidden=False, fanpage_id=None, template_json=None)

    # Classic template (should not change)
    cl = DesignTemplate(name="CL News", style="classic", category="news", is_hidden=False, fanpage_id=None, template_json={"brandDefaults": {"primary": "#000000"}})

    # User-created highlight template (should not change)
    user_hl = DesignTemplate(name="User HL", style="highlight", category="news", is_hidden=False, fanpage_id=1, template_json={"brandDefaults": {"primary": "#111111"}})

    # Hidden highlight template (should not change)
    hidden_hl = DesignTemplate(name="Hidden HL", style="highlight", category="news", is_hidden=True, fanpage_id=None, template_json={"brandDefaults": {"primary": "#222222"}})

    db_session.add_all([hl1, hl2, hl3, cl, user_hl, hidden_hl])
    db_session.commit()

    # Valid update
    resp = client.put("/templates/styles/highlight/defaults", json={"primary": "#22D3EE", "quoteMarks": "#FEFEFC"})
    assert resp.status_code == 200
    res_data = resp.json()
    assert res_data["style"] == "highlight"
    assert res_data["defaults"] == {"primary": "#22D3EE", "quoteMarks": "#FEFEFC"}

    # Refresh and check db
    db_session.expire_all()
    for t in (hl1, hl2, hl3):
        row = db_session.query(DesignTemplate).filter_by(id=t.id).one()
        assert row.template_json["brandDefaults"] == {"primary": "#22D3EE", "quoteMarks": "#FEFEFC"}

    cl_row = db_session.query(DesignTemplate).filter_by(id=cl.id).one()
    assert cl_row.template_json["brandDefaults"] == {"primary": "#000000"}

    user_hl_row = db_session.query(DesignTemplate).filter_by(id=user_hl.id).one()
    assert user_hl_row.template_json["brandDefaults"] == {"primary": "#111111"}

    hidden_hl_row = db_session.query(DesignTemplate).filter_by(id=hidden_hl.id).one()
    assert hidden_hl_row.template_json["brandDefaults"] == {"primary": "#222222"}

    # Bad hex -> 422
    resp_bad = client.put("/templates/styles/highlight/defaults", json={"primary": "not-a-hex"})
    assert resp_bad.status_code == 422

    # Unknown style -> 404
    resp_unknown = client.put("/templates/styles/unknown_style/defaults", json={"primary": "#22D3EE"})
    assert resp_unknown.status_code == 404
    assert resp_unknown.json()["detail"] == "Unknown style"


def test_get_templates_serialization(db_session, client):
    t_shared = DesignTemplate(
        name="Custom Shared",
        style="gp",
        is_hidden=False,
        fanpage_id=None,
        category="news",
        template_json={"brandDefaults": {"primary": "#123456"}},
    )
    t_legacy_shared = DesignTemplate(
        name="MotoGP News (two-tone headline)",
        style=None,
        is_hidden=True,
        fanpage_id=None,
        category="news",
    )
    t_legacy_fp = DesignTemplate(
        name="MotoGP News (two-tone headline)",
        style=None,
        is_hidden=False,
        fanpage_id=1,
        category="news",
    )

    db_session.add_all([t_shared, t_legacy_shared, t_legacy_fp])
    db_session.commit()

    resp = client.get("/templates")
    assert resp.status_code == 200
    items = {item["id"]: item for item in resp.json()}

    # Check t_shared
    s_data = items[t_shared.id]
    assert s_data["style"] == "gp"
    assert s_data["is_hidden"] is False
    assert s_data["brand_defaults"] == {"primary": "#123456"}
    assert s_data["replacement_style"] is None

    # Check t_legacy_shared
    ls_data = items[t_legacy_shared.id]
    assert ls_data["style"] is None
    assert ls_data["is_hidden"] is True
    assert ls_data["brand_defaults"] is None
    assert ls_data["replacement_style"] == "gp"

    # Check t_legacy_fp (fanpage_id is not None -> replacement_style is None)
    lfp_data = items[t_legacy_fp.id]
    assert lfp_data["replacement_style"] is None


def test_put_template_is_default_category_scoping(db_session, client):
    t_news_1 = DesignTemplate(name="News 1", fanpage_id=1, category="news", is_default=True)
    t_news_2 = DesignTemplate(name="News 2", fanpage_id=1, category="news", is_default=False)
    t_quote = DesignTemplate(name="Quote 1", fanpage_id=1, category="quote", is_default=True)
    t_uncat_1 = DesignTemplate(name="Uncat 1", fanpage_id=1, category=None, is_default=True)
    t_uncat_2 = DesignTemplate(name="Uncat 2", fanpage_id=1, category=None, is_default=False)

    db_session.add_all([t_news_1, t_news_2, t_quote, t_uncat_1, t_uncat_2])
    db_session.commit()

    # Set t_news_2 as default: should unset t_news_1, but leave t_quote and t_uncat_1 as default
    resp = client.put(f"/templates/{t_news_2.id}", json={"is_default": True})
    assert resp.status_code == 200

    db_session.expire_all()
    assert db_session.query(DesignTemplate).filter_by(id=t_news_2.id).one().is_default is True
    assert db_session.query(DesignTemplate).filter_by(id=t_news_1.id).one().is_default is False
    assert db_session.query(DesignTemplate).filter_by(id=t_quote.id).one().is_default is True
    assert db_session.query(DesignTemplate).filter_by(id=t_uncat_1.id).one().is_default is True

    # Set t_uncat_2 as default: should unset t_uncat_1, but leave others intact
    resp2 = client.put(f"/templates/{t_uncat_2.id}", json={"is_default": True})
    assert resp2.status_code == 200

    db_session.expire_all()
    assert db_session.query(DesignTemplate).filter_by(id=t_uncat_2.id).one().is_default is True
    assert db_session.query(DesignTemplate).filter_by(id=t_uncat_1.id).one().is_default is False
    assert db_session.query(DesignTemplate).filter_by(id=t_news_2.id).one().is_default is True
    assert db_session.query(DesignTemplate).filter_by(id=t_quote.id).one().is_default is True


def test_put_fanpage_style_template_defaults(db_session, client):
    # Classic shared templates
    cl_news = DesignTemplate(name="Classic News", style="classic", category="news", is_hidden=False, fanpage_id=None)
    cl_quote = DesignTemplate(name="Classic Quote", style="classic", category="quote", is_hidden=False, fanpage_id=None)
    cl_disc = DesignTemplate(name="Classic Disc", style="classic", category="discussion", is_hidden=False, fanpage_id=None)

    # User quote template
    user_quote = DesignTemplate(name="My Custom Quote", style=None, category="quote", is_hidden=False, fanpage_id=1)

    fp = TargetFanpage(id=1, name="Test Fanpage", is_active=True)

    db_session.add_all([cl_news, cl_quote, cl_disc, user_quote, fp])
    db_session.commit()

    # 1. Setting template_style = "classic" sets all 3 default ids
    resp1 = client.put(f"/fanpages/{fp.id}", json={"template_style": "classic"})
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["template_style"] == "classic"
    assert data1["default_news_template_id"] == cl_news.id
    assert data1["default_quote_template_id"] == cl_quote.id
    assert data1["default_discussion_template_id"] == cl_disc.id

    db_session.expire_all()
    fp_row = db_session.query(TargetFanpage).filter_by(id=fp.id).one()
    assert fp_row.template_style == "classic"
    assert fp_row.default_news_template_id == cl_news.id
    assert fp_row.default_quote_template_id == cl_quote.id
    assert fp_row.default_discussion_template_id == cl_disc.id

    # 2. Setting template_style = "classic" with an explicit default_quote_template_id keeps the user quote id
    resp2 = client.put(
        f"/fanpages/{fp.id}",
        json={"template_style": "classic", "default_quote_template_id": user_quote.id},
    )
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["template_style"] == "classic"
    assert data2["default_news_template_id"] == cl_news.id
    assert data2["default_quote_template_id"] == user_quote.id
    assert data2["default_discussion_template_id"] == cl_disc.id

    db_session.expire_all()
    fp_row2 = db_session.query(TargetFanpage).filter_by(id=fp.id).one()
    assert fp_row2.default_quote_template_id == user_quote.id
