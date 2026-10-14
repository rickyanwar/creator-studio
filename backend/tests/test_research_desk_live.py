"""Tests for Muse research desk live mode (TASK 2)."""
import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_current_user
from app.main import app
from app.models.ai_video import VideoIdea
from app.services import research_desk
from tests.test_research_desk import clean, db, make_fanpage, pack, setup_db, token


@pytest.fixture(autouse=True)
def restore_pilot_mode(db):
    yield
    row = research_desk.get_settings_row(db)
    row.muse_research_mode = "pilot"
    db.commit()


def test_live_brief_mode_and_need_ignores_review_cap(db, clean):
    row = research_desk.get_settings_row(db)
    row.muse_research_mode = "live"
    row.muse_pilot_packs = 1
    row.ai_video_idea_bank_days = 3
    db.commit()

    fp = make_fanpage(db, clean, ai_video_daily_count=1)

    client = TestClient(app)
    res = client.get("/research/brief", headers=token(db)).json()
    assert res["mode"] == "live"
    matching = [c for n in res["niches"] for c in n["channels"] if c["channel_id"] == fp.id]
    assert len(matching) == 1
    assert matching[0]["ideas_needed"] == 3


def test_live_pack_is_approved_when_score_reaches_minimum(db, clean):
    row = research_desk.get_settings_row(db)
    row.muse_research_mode = "live"
    db.commit()

    fp = make_fanpage(db, clean)
    fp.ai_video_min_score = 65
    db.commit()
    p = pack([fp.id])
    assert p["score"] >= 70

    client = TestClient(app)
    res = client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]}).json()
    assert res["results"][0]["status"] == "created"

    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()
    assert idea.status == "approved"
    assert idea.decided_by == "muse-live"
    assert idea.decided_at is not None


def test_live_pack_below_minimum_is_rejected(db, clean):
    row = research_desk.get_settings_row(db)
    row.muse_research_mode = "live"
    db.commit()

    fp = make_fanpage(db, clean)
    fp.ai_video_min_score = 99
    db.commit()
    p = pack([fp.id])
    assert p["score"] < 99

    client = TestClient(app)
    res = client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]}).json()
    result = res["results"][0]
    assert result["status"] == "rejected"
    assert "score must be at least 99" in " ".join(result["variants"][0]["errors"])


def test_pilot_still_holds_ideas(db, clean):
    row = research_desk.get_settings_row(db)
    assert row.muse_research_mode == "pilot"

    fp = make_fanpage(db, clean)
    p = pack([fp.id])

    client = TestClient(app)
    res = client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]}).json()
    assert res["results"][0]["status"] == "created"

    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()
    assert idea.status == "pilot"


def test_switch_to_live_releases_pilot_ideas(db, clean):
    row = research_desk.get_settings_row(db)
    assert row.muse_research_mode == "pilot"

    fp = make_fanpage(db, clean)
    fp.ai_video_min_score = 65
    db.commit()
    p = pack([fp.id])
    assert p["score"] >= 65

    client = TestClient(app)
    res = client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]}).json()
    assert res["results"][0]["status"] == "created"

    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()
    assert idea.status == "pilot"

    app.dependency_overrides[get_current_user] = lambda: "owner"
    try:
        put_res = client.put("/settings", json={"muse_research_mode": "live"})
        assert put_res.status_code == 200
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    db.refresh(idea)
    assert idea.status == "approved"
    assert idea.decided_by == "muse-live"
    assert idea.decided_at is not None


def test_invalid_mode_rejected(db, clean):
    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: "owner"
    try:
        put_res = client.put("/settings", json={"muse_research_mode": "auto"})
        assert put_res.status_code == 422
    finally:
        app.dependency_overrides.pop(get_current_user, None)
