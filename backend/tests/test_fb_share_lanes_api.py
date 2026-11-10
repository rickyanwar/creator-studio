"""Group Share lane API: lanes are hidden from lists but editable by id."""

import json
import sqlite3

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.database import Base
from app.main import app
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.fb_share import lanes

sqlite3.register_adapter(list, json.dumps)


@pytest.fixture
def db(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_ARRAY", lambda self, type_, **kw: "TEXT", raising=False)
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_JSONB", lambda self, type_, **kw: "TEXT", raising=False)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    tables = [Base.metadata.tables[name] for name in (
        "target_fanpages", "fanpage_sources", "fanpage_news_sources", "discussion_topics",
        "discussion_content_ideas", "pinterest_sources", "pinterest_content_ideas",
        "facebook_photo_sources", "facebook_photo_ideas", "yt_clip_sources", "social_destinations",
        "fb_share_brand_settings", "fb_share_lanes", "fb_share_group_jobs",
    )]
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))):
        Base.metadata.create_all(engine, tables=tables)
    with sessionmaker(bind=engine)() as session:
        session.add_all([TargetFanpage(id=1, name="Brand", accent_color="#111111"), TargetFanpage(id=2, name="Zeta")])
        session.commit()
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


def test_lane_endpoints_create_once_and_reject_lanes(client, db):
    assert client.get("/fanpages/1/group-share/lane").json() == {"lane_fanpage_id": None}
    created = client.post("/fanpages/1/group-share/lane")
    assert created.status_code == 200, created.text
    lane_id = created.json()["lane_fanpage_id"]
    assert client.post("/fanpages/1/group-share/lane").json() == {"lane_fanpage_id": lane_id}
    assert client.get("/fanpages/1/group-share/lane").json() == {"lane_fanpage_id": lane_id}
    assert client.post(f"/fanpages/{lane_id}/group-share/lane").status_code == 400
    assert client.post("/fanpages/999/group-share/lane").status_code == 404
    assert client.get("/fanpages/999/group-share/lane").status_code == 404


def test_lists_hide_lane_but_detail_and_update_work(client, db):
    lane_id = client.post("/fanpages/1/group-share/lane").json()["lane_fanpage_id"]
    assert [row["id"] for row in client.get("/fanpages").json()] == [1, 2]
    assert [row["id"] for row in client.get("/content-projects").json()] == [1, 2]
    detail = client.get(f"/fanpages/{lane_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["name"] == "Brand (groups)"
    updated = client.put(f"/fanpages/{lane_id}", json={"discussion_enabled": True, "discussion_daily_count": 4})
    assert updated.status_code == 200, updated.text
    db.expire_all()
    assert db.get(TargetFanpage, lane_id).discussion_daily_count == 4
    assert db.get(TargetFanpage, 1).discussion_enabled is False


def test_brand_save_syncs_look_to_lane(client, db):
    lane_id = client.post("/fanpages/1/group-share/lane").json()["lane_fanpage_id"]
    client.put(f"/fanpages/{lane_id}", json={"discussion_daily_count": 4})
    response = client.put("/fanpages/1", json={"accent_color": "#22AA22", "discussion_daily_count": 9})
    assert response.status_code == 200, response.text
    db.expire_all()
    lane = db.get(TargetFanpage, lane_id)
    assert lane.accent_color == "#22AA22"
    assert lane.discussion_daily_count == 4


def test_group_share_switch_keeps_lane_active_in_sync(client, db):
    lane_id = client.post("/fanpages/1/group-share/lane").json()["lane_fanpage_id"]
    assert db.get(TargetFanpage, lane_id).is_active is False
    assert client.put("/fanpages/1/group-share", json={"enabled": True, "settings": {}}).status_code == 200
    db.expire_all()
    assert db.get(TargetFanpage, lane_id).is_active is True
    assert client.put("/fanpages/1/group-share", json={"enabled": False, "settings": {}}).status_code == 200
    db.expire_all()
    assert db.get(TargetFanpage, lane_id).is_active is False
    assert lanes.lane_brand_id(db, lane_id) == 1
