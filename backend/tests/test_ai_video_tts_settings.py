"""Mode 8 narration settings, voice selection, and authenticated API coverage."""
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.database import Base
from app.main import app
from app.models.target_fanpages import TargetFanpage
from app.schemas.fanpage import FanpageUpdate
from app.services.tts_voices import TTS_VOICES, auto_voice, narration_choice, tts_allowed
from app.services.video_templates import get_template


@pytest.mark.parametrize("template,preset,expected", [
    ("health_3d_explainer", "EN-DOC", "Iapetus"),
    ("motorcycle_commercial", "EN-DOC", "Algenib"),
    ("motorsport_legend_innovation", "EN-DOC", "Algenib"),
    ("motorsport_legend_innovation", "EN-JOURNO-F", "Kore"),
    ("motorsport_tech_explainer", "EN-JOURNO-F", "Kore"),
    ("motorsport_tech_explainer", "EN-PADDOCK", "Iapetus"),
    ("motorsport_tech_explainer", "EN-AUSSIE-ENG", "Iapetus"),
    ("motorsport_tech_explainer", "EN-US-ENG", "Iapetus"),
    ("motorsport_tech_explainer", "EN-SCOT", "Iapetus"),
    ("motorsport_tech_explainer", "unknown", "Iapetus"),
    ("motorsport_tech_explainer", None, "Iapetus"),
])
def test_auto_voice(template, preset, expected):
    assert auto_voice(template, preset) == expected


@pytest.mark.parametrize("options,expected", [([1], False), ([2, 3], True), ([], False)])
def test_tts_allowed(options, expected):
    assert tts_allowed({"clip_options": options}) is expected


def test_tts_allowed_registry_template():
    assert tts_allowed(get_template("health_3d_explainer")) is True
    assert tts_allowed(get_template("motorsport_tech_dive")) is False


@pytest.mark.parametrize("settings", [None, [], "tts", {"key": "tts"},
    {"key": {"engine": "unknown"}}, {"key": {"engine": []}},
    {"key": {"engine": "tts", "voice": "unknown"}},
    {"key": {"engine": "tts", "voice": []}}])
def test_narration_choice_invalid(settings):
    assert narration_choice(SimpleNamespace(ai_video_narration=settings), "key") == {
        "engine": "flow", "voice": None, "bed": None,
    }


def test_narration_choice_missing_and_valid():
    assert narration_choice(SimpleNamespace(), "key") == {"engine": "flow", "voice": None, "bed": None}
    for engine in ("flow", "tts", "asmr"):
        for voice in (None, "Charon"):
            settings = {"key": {"engine": engine, "voice": voice}}
            assert narration_choice(SimpleNamespace(ai_video_narration=settings), "key") == {**settings["key"], "bed": None}


def test_schema_normalizes_settings():
    settings = {
        "health_3d_explainer": {"engine": "tts", "voice": "Kore", "ignored": True},
        "motorsport_tech_dive": {"engine": "flow"},
    }
    assert FanpageUpdate(ai_video_narration=settings).ai_video_narration == {
        "health_3d_explainer": {"engine": "tts", "voice": "Kore"},
        "motorsport_tech_dive": {"engine": "flow", "voice": None},
    }


@pytest.mark.parametrize("voice", [None, "Kore", "unknown", []])
def test_schema_normalizes_asmr_voice(voice):
    settings = {"health_3d_explainer": {"engine": "asmr", "voice": voice}}
    assert FanpageUpdate(ai_video_narration=settings).ai_video_narration == {
        "health_3d_explainer": {"engine": "asmr", "voice": None},
    }


def test_schema_rejects_asmr_for_single_clip_template():
    with pytest.raises(ValidationError, match="at least 2 clips"):
        FanpageUpdate(ai_video_narration={"motorsport_tech_dive": {"engine": "asmr"}})


@pytest.mark.parametrize("key,choice,message", [
    ("unknown", {"engine": "flow"}, "unknown template key"),
    ("health_3d_explainer", {"engine": "tts", "voice": "unknown"}, "unknown TTS voice"),
    ("health_3d_explainer", {"engine": "tts", "voice": []}, "unknown TTS voice"),
    ("health_3d_explainer", {"engine": "unknown"}, "engine must be"),
    ("health_3d_explainer", {"engine": []}, "engine must be"),
    ("health_3d_explainer", "tts", "must be a dict"),
    ("motorsport_tech_dive", {"engine": "tts"}, "at least 2 clips"),
])
def test_schema_rejects_invalid_settings(key, choice, message):
    with pytest.raises(ValidationError, match=message):
        FanpageUpdate(ai_video_narration={key: choice})


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_ARRAY", lambda self, type_, **kw: "TEXT", raising=False)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [Base.metadata.tables[name] for name in (
        "target_fanpages", "fanpage_sources", "fanpage_news_sources", "discussion_topics",
        "discussion_content_ideas", "pinterest_sources", "pinterest_content_ideas",
        "facebook_photo_sources", "facebook_photo_ideas", "yt_clip_sources",
    )]
    Base.metadata.create_all(engine, tables=tables)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture
def client(db):
    previous = app.dependency_overrides.copy()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: "admin"
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        app.dependency_overrides.update(previous)


def test_fanpage_narration_persists(client, db):
    fanpage = TargetFanpage(name="TTS settings")
    db.add(fanpage)
    db.commit()
    settings = {"health_3d_explainer": {"engine": "tts", "voice": "Sadaltager"}}
    response = client.put(f"/fanpages/{fanpage.id}", json={"ai_video_narration": settings})
    assert response.status_code == 200, response.text
    assert response.json()["ai_video_narration"] == settings
    db.expire_all()
    assert db.get(TargetFanpage, fanpage.id).ai_video_narration == settings
    response = client.get(f"/fanpages/{fanpage.id}")
    assert response.status_code == 200, response.text
    assert response.json()["ai_video_narration"] == settings


def test_tts_voices_endpoint(client):
    response = client.get("/ai-video/tts-voices")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) == 30
    assert rows == [{"name": name, **TTS_VOICES[name]} for name in sorted(TTS_VOICES)]


def test_templates_include_auto_voice(client):
    response = client.get("/ai-video/templates")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert all("tts_auto_voice" in row for row in rows)
    templates = {row["key"]: row for row in rows}
    assert templates["health_3d_explainer"]["tts_auto_voice"] == "Iapetus"
    assert templates["motorcycle_commercial"]["tts_auto_voice"] == "Algenib"
    assert templates["motorsport_tech_dive"]["tts_auto_voice"] is None


def test_fanpage_asmr_narration_persists(client, db):
    fanpage = TargetFanpage(name="ASMR settings")
    db.add(fanpage)
    db.commit()
    settings = {"health_3d_explainer": {"engine": "asmr", "voice": "Kore"}}
    expected = {"health_3d_explainer": {"engine": "asmr", "voice": None}}
    response = client.put(f"/fanpages/{fanpage.id}", json={"ai_video_narration": settings})
    assert response.status_code == 200, response.text
    assert response.json()["ai_video_narration"] == expected
    db.expire_all()
    assert db.get(TargetFanpage, fanpage.id).ai_video_narration == expected
    response = client.get(f"/fanpages/{fanpage.id}")
    assert response.status_code == 200, response.text
    assert response.json()["ai_video_narration"] == expected
