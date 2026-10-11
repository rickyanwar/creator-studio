from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_fill, ai_video_ideas, api_tokens
from app.services.video_templates import get_template, required_variables


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db_session():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def fanpage(db_session):
    fp = TargetFanpage(
        repliz_account_id=f"v5-{id(db_session)}-{datetime.now().timestamp()}",
        name="V5 F1 Page",
        is_active=True,
        is_connected=True,
        timezone="America/New_York",
        mode2_gallery_niches=["f1"],
        ai_video_enabled=True,
        ai_video_daily_count=1,
        ai_video_min_score=70,
        ai_video_clips=3,
        caption_language="en",
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    yield fp
    db_session.delete(fp)
    db_session.commit()


def idea_data(topic="DRS mechanics", **overrides):
    data = {"template_key": "motorsport_tech_explainer", "topic": topic}
    data.update(overrides)
    return data


def test_idea_validation_and_lifecycle(db_session, fanpage):
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="ab"), "owner")
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(clips=1), "owner")
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(score=101), "owner")
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(score=True), "owner")
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(evidence={"x": "a" * 4100}), "owner")
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(variables={"x": "a" * 8200}), "owner")

    owner = ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="Owner mechanism"), "owner", username="admin")
    assert owner.status == "approved"
    hermes = ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="Hermes mechanism", score=80, rationale="Useful"), "hermes")
    assert hermes.status == "proposed"
    with pytest.raises(ValueError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="No score", rationale="why"), "hermes")

    proposed = [ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic=f"Limit topic {i}", score=1, rationale="r"), "hermes") for i in range(29)]
    assert len(proposed) == 29
    with pytest.raises(ai_video_ideas.LimitExceededError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="Limit topic final", score=1, rationale="r"), "hermes")


def test_duplicates_rejected_topics_do_not_count(db_session, fanpage):
    first = ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="Rear wing airflow"), "owner")
    with pytest.raises(ai_video_ideas.ConflictError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="rear wing, airflow"), "owner")
    first.status = "rejected"
    db_session.commit()
    second = ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="rear wing airflow"), "owner")
    second.status = "proposed"
    db_session.commit()
    with pytest.raises(ai_video_ideas.ConflictError):
        ai_video_ideas.approve(db_session, first, "admin")
    assert ai_video_ideas.approve(db_session, second, "admin").status == "approved"
    with pytest.raises(ai_video_ideas.ConflictError):
        ai_video_ideas.reject(db_session, second, "admin")


def test_proposed_limit_and_decision_guards(db_session, fanpage):
    for i in range(30):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic=f"Proposed {i}", score=5, rationale="r"), "hermes")
    with pytest.raises(ai_video_ideas.LimitExceededError):
        ai_video_ideas.create_idea(db_session, fanpage.id, idea_data(topic="Proposed overflow", score=5, rationale="r"), "hermes")


def test_fill_prompt_niche_and_parse_formats(fanpage):
    template = get_template("motorsport_tech_explainer")
    idea = SimpleNamespace(topic="topic", angle=None, research_notes=None, variables=None)
    assert '"f1_red"' in ai_video_fill.build_fill_prompt(template, idea, fanpage, 2)
    fanpage.mode2_gallery_niches = ["MotoGP"]
    assert '"motogp"' in ai_video_fill.build_fill_prompt(template, idea, fanpage, 2)

    payload = '{"variables": {}, "title": "Title", "caption": "Caption"}'
    for raw in (payload, "```json\n" + payload + "\n```", "Here is the result:\n" + payload + "\nThanks"):
        assert ai_video_fill._parse(raw)["title"] == "Title"
    with pytest.raises(ValueError):
        ai_video_fill._parse("not JSON")
    with pytest.raises(ValueError):
        ai_video_fill._parse('{"variables": {}, "title": "", "caption": "Caption"}')
    with pytest.raises(ValueError):
        ai_video_fill._parse('{"variables": {}, "title": "Title", "caption": "#a #b #c #d #e #f"}')


def _valid_variables():
    return {
        "vehicle_preset": "f1_red", "part": "rear wing", "mechanism_details": "the actuator", "mechanism_action": "the flap moves visibly",
        "narration_1": "This is a rear wing mechanism working inside a modern racing car during a fast lap on circuit.",
        "effect_visual": "Airflow lines glow around the assembly.", "parts_list": "the flap, actuator, bolts, and supports",
        "final_state": "the complete assembly locks back into place", "narration_2": "The parts separate clearly, then return together as the airflow settles around the car and the mechanism locks shut.",
    }


def test_fill_idea_mocked_and_variables_override(fanpage):
    idea = SimpleNamespace(id=1, template_key="motorsport_tech_explainer", topic="topic", angle=None, research_notes=None, variables=None)
    raw = {"variables": _valid_variables(), "title": "A title", "caption": "A caption"}
    with patch.object(ai_video_fill.news_copywriter, "_generate_with_fallback", return_value=(raw, "mock")):
        variables, title, caption, rendered, provider = ai_video_fill.fill_idea(None, idea, fanpage, 2)
    assert variables == raw["variables"] and title == "A title" and len(rendered) == 2 and provider == "mock"
    override = dict(_valid_variables(), part="override part")
    idea.variables = override
    model_raw = dict(raw, variables=dict(_valid_variables(), part="model part"))
    with patch.object(ai_video_fill.news_copywriter, "_generate_with_fallback", return_value=(model_raw, "mock")):
        variables, *_ = ai_video_fill.fill_idea(None, idea, fanpage, 2)
    assert variables["part"] == "override part"


def test_plan_limits_and_clip_choice(db_session, fanpage, monkeypatch):
    import app.tasks.ai_video_plan as planner
    monkeypatch.setattr(planner, "SessionLocal", lambda: db_session)
    from app.tasks import ai_video_plan
    approved = VideoIdea(fanpage_id=fanpage.id, template_key="motorsport_tech_explainer", topic="auto approve", score=90, rationale="r", status="proposed")
    db_session.add(approved)
    db_session.commit()
    with patch.object(planner, "fill_idea", return_value=(_valid_variables(), "Title", "Caption", [{"index": 1, "mode": "t2v", "prompt": "p"}, {"index": 2, "mode": "i2v", "prompt": "p"}, {"index": 3, "mode": "i2v", "prompt": "p"}], "mock")):
        ai_video_plan.plan_ai_videos.run()
    approved = db_session.query(VideoIdea).filter_by(topic="auto approve").one()
    assert approved.status == "queued"
    assert approved.decided_by == "auto"


def test_plan_fill_failure_and_clip_precedence(db_session, fanpage, monkeypatch):
    import app.tasks.ai_video_plan as planner
    monkeypatch.setattr(planner, "SessionLocal", lambda: db_session)
    idea = VideoIdea(fanpage_id=fanpage.id, template_key="motorsport_tech_explainer", topic="will fail", score=90, rationale="r", status="approved", clips=2)
    db_session.add(idea)
    db_session.commit()
    with patch.object(planner, "fill_idea", side_effect=ValueError("bad fill")):
        planner.plan_ai_videos.run()
    idea = db_session.query(VideoIdea).filter_by(topic="will fail").one()
    assert idea.status == "failed" and "bad fill" in idea.error


def _run_planner(db_session, monkeypatch, rendered=None, calls=None):
    import app.tasks.ai_video_plan as planner

    monkeypatch.setattr(planner, "SessionLocal", lambda: db_session)
    rendered = rendered or [{"index": 1, "mode": "t2v", "prompt": "rendered"}]

    def fake_fill(_db, idea, _fanpage, clips):
        if calls is not None:
            calls.append(clips)
        return {"part": "stored"}, "Generated title", "Generated caption", rendered, "mock"

    with patch.object(planner, "fill_idea", side_effect=fake_fill):
        planner.plan_ai_videos.run()


def _new_idea(db_session, fanpage, topic, **kwargs):
    idea = VideoIdea(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic=topic,
        status="approved",
        **kwargs,
    )
    db_session.add(idea)
    db_session.commit()
    return idea


def _job(db_session, fanpage, created_at, status="queued"):
    job = VideoJob(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Existing",
        caption="Existing",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[],
        credits_per_clip=15,
        credits_estimate=30,
        status=status,
        created_at=created_at,
    )
    db_session.add(job)
    db_session.commit()
    return job


def test_planner_uses_fanpage_local_day(db_session, fanpage, monkeypatch):
    local_tz = ZoneInfo("America/New_York")
    local_midnight = datetime.combine(datetime.now(local_tz).date(), datetime.min.time(), tzinfo=local_tz)
    _new_idea(db_session, fanpage, "local day after midnight")
    _job(db_session, fanpage, (local_midnight + timedelta(hours=1)).astimezone(timezone.utc).replace(tzinfo=None))
    _run_planner(db_session, monkeypatch)
    assert db_session.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id).count() == 1

    db_session.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id).delete(synchronize_session=False)
    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete(synchronize_session=False)
    db_session.commit()
    _new_idea(db_session, fanpage, "local day before midnight")
    _job(db_session, fanpage, (local_midnight - timedelta(hours=1)).astimezone(timezone.utc).replace(tzinfo=None))
    _run_planner(db_session, monkeypatch)
    assert db_session.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id).count() == 2


def test_planner_open_cap_blocks_new_job(db_session, fanpage, monkeypatch):
    _new_idea(db_session, fanpage, "open cap")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    _job(db_session, fanpage, now, "queued")
    _job(db_session, fanpage, now, "claimed")
    _run_planner(db_session, monkeypatch)
    assert db_session.query(VideoJob).filter(VideoJob.fanpage_id == fanpage.id).count() == 2


@pytest.mark.parametrize("min_score, score", [(None, 100), (70, 60)])
def test_planner_min_score_does_not_auto_approve(db_session, fanpage, monkeypatch, min_score, score):
    fanpage.ai_video_min_score = min_score
    db_session.commit()
    idea = VideoIdea(fanpage_id=fanpage.id, template_key="motorsport_tech_explainer", topic=f"below threshold {min_score}", score=score, status="proposed")
    db_session.add(idea)
    db_session.commit()
    idea_id = idea.id
    _run_planner(db_session, monkeypatch)
    db_session.expire_all()
    assert db_session.query(VideoIdea).filter_by(id=idea_id).one().status == "proposed"
    assert db_session.query(VideoJob).filter_by(fanpage_id=fanpage.id).count() == 0


@pytest.mark.parametrize(
    "idea_clips, page_clips, expected",
    [(3, 2, 3), (None, 3, 3), (None, 5, 2)],
)
def test_planner_clip_precedence_and_created_job(db_session, fanpage, monkeypatch, idea_clips, page_clips, expected):
    fanpage.ai_video_clips = page_clips
    db_session.commit()
    idea = _new_idea(db_session, fanpage, f"clip precedence {idea_clips}-{page_clips}", clips=idea_clips)
    idea_id = idea.id
    calls = []
    rendered = [{"index": 1, "mode": "t2v", "prompt": "one"}, {"index": 2, "mode": "i2v", "prompt": "two"}]
    _run_planner(db_session, monkeypatch, rendered=rendered, calls=calls)
    assert calls == [expected]
    db_session.expire_all()
    job = db_session.query(VideoJob).filter_by(idea_id=idea_id).one()
    stored_idea = db_session.query(VideoIdea).filter_by(id=idea_id).one()
    assert job.status == "queued"
    assert job.credits_estimate == expected * 15
    assert job.model == "omni-flash"
    assert job.clips == rendered
    assert stored_idea.status == "queued"
    assert stored_idea.variables == {"part": "stored"}


def test_hermes_video_ideas_over_limit_returns_429(db_session, fanpage):
    from app.api.deps import get_current_user

    for i in range(30):
        db_session.add(VideoIdea(fanpage_id=fanpage.id, template_key="motorsport_tech_explainer", topic=f"HTTP limit {i}", score=50, rationale="r", status="proposed", source="hermes"))
    db_session.commit()
    app.dependency_overrides[get_db] = lambda: db_session
    token_db, token = api_tokens.create_token(db_session, "v5-limit-write", ["ideas:write"])
    app.dependency_overrides[get_current_user] = lambda: "admin"
    response = TestClient(app).post(
        "/hermes/video-ideas",
        json={"fanpage_id": fanpage.id, "template_key": "motorsport_tech_explainer", "topic": "HTTP limit overflow", "score": 80, "rationale": "r"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 429
    app.dependency_overrides.clear()


def test_http_admin_and_hermes(db_session, fanpage):
    from app.api.deps import get_current_user
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin"
    read_db, read_token = api_tokens.create_token(db_session, "v5-read", ["read"])
    write_db, write_token = api_tokens.create_token(db_session, "v5-write", ["ideas:write"])
    client = TestClient(app)
    assert client.get("/ai-video/templates").status_code == 200
    response = client.post("/ai-video/ideas", json={"fanpage_id": fanpage.id, "template_key": "motorsport_tech_explainer", "topic": "HTTP admin idea"})
    assert response.status_code == 201
    idea_id = response.json()["id"]
    assert client.get("/ai-video/ideas").status_code == 200
    assert client.post(f"/ai-video/ideas/{idea_id}/reject").status_code == 409

    headers_read = {"Authorization": f"Bearer {read_token}"}
    headers_write = {"Authorization": f"Bearer {write_token}"}
    assert client.get("/hermes/video-templates", headers=headers_read).status_code == 200
    payload = {"fanpage_id": fanpage.id, "template_key": "motorsport_tech_explainer", "topic": "HTTP Hermes idea", "score": 80, "rationale": "r"}
    assert client.post("/hermes/video-ideas", json=payload, headers=headers_write).status_code == 201
    assert client.post("/hermes/video-ideas", json=payload, headers=headers_read).status_code == 403
    assert client.post("/hermes/video-ideas", json=payload, headers=headers_write).status_code == 409
    assert client.post("/hermes/video-ideas", json={**payload, "fanpage_id": 999999}, headers=headers_write).status_code == 404
    app.dependency_overrides.clear()
