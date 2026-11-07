"""Engine attribution for prompt polish read endpoints."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.api.prompt_polish import polish_engine, router
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage


@pytest.mark.parametrize("account,expected", [
    ("chatgpt:utama", "chatgpt"),
    ("codex:cx/gpt-6.1-sol", "codex"),
    ("andis", "argon"), ("budi", "argon"),
    ("tiska", "argon"), ("mamiqq", "argon"),
    (None, None), ("", None), (42, None), ({}, None),
])
def test_polish_engine_mapping(account, expected):
    assert polish_engine(account) == expected


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(SQLiteTypeCompiler, "visit_ARRAY", lambda self, type_, **kw: "TEXT", raising=False)
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    column = VideoJob.__table__.c.clips_done
    original = column.server_default
    try:
        column.server_default = None
        for model in (TargetFanpage, VideoIdea, VideoJob):
            model.__table__.create(engine)
    finally:
        column.server_default = original
    app = FastAPI()
    app.include_router(router)
    with Session(engine) as session:
        app.dependency_overrides[get_db] = lambda: session
        app.dependency_overrides[get_current_user] = lambda: "testuser"
        with TestClient(app) as c:
            yield c, session
    engine.dispose()


def test_polish_engine_endpoints(client):
    c, session = client
    fp = TargetFanpage(name="Engine test", repliz_account_id="engine-test", ai_video_prompt_polish_enabled=False)
    session.add(fp)
    session.flush()
    preparations = [{"account": "chatgpt:utama"}, {"account": "codex:cx/gpt-6.1-sol"}, {"account": "andis"}, None, [], {"account": 42}]
    ideas = [VideoIdea(fanpage_id=fp.id, topic="Test", template_key="motorsport_tech_explainer", prepared=prep) for prep in preparations]
    session.add_all(ideas)
    session.flush()
    jobs = [VideoJob(
        fanpage_id=fp.id, idea_id=idea_id, title="Test", caption="Caption",
        template_key="motorsport_tech_explainer", clips=[], model="veo-3",
        aspect="9:16", clip_seconds=5, credits_per_clip=10, credits_estimate=30,
    ) for idea_id in [i.id for i in ideas] + [None]]
    session.add_all(jobs)
    session.commit()
    expected = ["chatgpt", "codex", "argon", None, None, None]
    ids = ",".join(str(i.id) for i in ideas)
    response = c.get("/prompt-polish/ideas-engine", params={"ids": f"invalid, {ids},999999"})
    assert response.status_code == 200
    assert response.json() == {str(i.id): e for i, e in zip(ideas, expected)}
    assert c.get("/prompt-polish/ideas-engine").json() == {}
    assert c.get("/prompt-polish/ideas-engine?ids=bad,-1").json() == {}
    capped = ",".join([str(ideas[0].id)] * 200 + [str(ideas[1].id)])
    assert c.get("/prompt-polish/ideas-engine", params={"ids": capped}).json() == {str(ideas[0].id): "chatgpt"}
    assert c.get("/prompt-polish/ideas-state", params={"ids": ids}).json() == {str(i.id): "off" for i in ideas}
    for idea, engine_name in zip(ideas, expected):
        assert c.get(f"/prompt-polish/ideas/{idea.id}").json()["engine"] == engine_name
    for job, engine_name in zip(jobs, expected + [None]):
        assert c.get(f"/prompt-polish/jobs/{job.id}").json()["engine"] == engine_name
