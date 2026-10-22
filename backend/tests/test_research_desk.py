"""Research desk (Muse) pilot: brief, pack checks, pilot storage, feedback and the no-leak rule."""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import VideoIdea
from app.models.research import ResearchCall, ResearchFeedback
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_ideas, api_tokens, research_desk
from app.services.real_names import find_real_names
from app.services.video_templates import get_template

LEAK_NAME = "Leaky Paddock Page"


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def clean(db):
    """Only this test's fanpages take part in research; reset the research settings."""
    others = db.query(TargetFanpage).filter(TargetFanpage.ai_video_enabled == True).all()  # noqa: E712
    for fp in others:
        fp.ai_video_enabled = False
    row = research_desk.get_settings_row(db)
    row.ai_video_researcher, row.muse_research_mode, row.muse_pilot_packs = "muse", "pilot", 3
    row.research_references, row.research_last_report = None, None
    db.commit()
    created = []
    yield created
    db.rollback()
    ids = [fp_id for fp_id in created]
    from app.models.ai_video import VideoJob
    db.query(VideoJob).filter(VideoJob.fanpage_id.in_(ids)).delete(synchronize_session=False)
    db.query(ResearchFeedback).delete(synchronize_session=False)
    db.query(VideoIdea).filter(VideoIdea.fanpage_id.in_(ids)).delete(synchronize_session=False)
    db.query(TargetFanpage).filter(TargetFanpage.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def make_fanpage(db, created, name=LEAK_NAME, niches=("f1",), **kw):
    fp = TargetFanpage(
        repliz_account_id=f"rd-{uuid.uuid4().hex[:12]}", name=name, is_active=True, ai_video_enabled=True,
        mode2_gallery_niches=list(niches), caption_language="en", target_country="GB", ai_video_clips=3, ai_video_clips_min=2,
        ai_video_min_score=70, **kw,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    created.append(fp.id)
    return fp


def token(db, scopes=("research",)):
    _, plaintext = api_tokens.create_token(db, f"rd-{uuid.uuid4().hex[:6]}", list(scopes))
    return {"Authorization": f"Bearer {plaintext}"}


NARRATION = {
    1: "Engineers moved the hinge forward so the upper flap now tilts on every straight and closes again before the braking zone arrives.",
    2: "With the flap open the slot lets air slip through, drag falls away, and the car reaches its top speed far sooner than before.",
    3: "That is why the new rules matter: a simple pivot changes how every lap is driven, from the long straights to the slowest corners.",
}
NARRATION_B = {
    1: "Watch the rear wing closely, because its top element swings upward on the straights and snaps shut the moment the driver brakes.",
    2: "Opening that gap lets the airflow escape, cutting the drag force so the machine accelerates harder down the longest flat-out sections.",
    3: "One small moving element now reshapes race strategy, rewarding teams that tune it well and punishing anyone who gets it wrong.",
}


def variables(clips=2, narration=NARRATION, preset="f1"):
    v = {
        "part": "rear wing", "vehicle_preset": preset,
        "mechanism_details": "a slim actuator cylinder inside the central pillar and the upper flap hinge",
        "mechanism_action": "The actuator pushes and the upper flap pivots upward around its forward hinge while the main plane stays fixed.",
        "moving_part": "upper rear-wing flap",
        "fixed_parts": "the lower main plane and both endplates",
        "effect_visual": "Glowing white airflow lines rush through the open slot of the rear wing.",
        "parts_list": "the endplates, the main plane, the upper flap, the actuator",
        "final_state": "the rear wing closes its flap",
    }
    for i in range(1, clips + 1):
        v[f"narration_{i}"] = narration[i]
    if clips == 3:
        v["payoff_action"] = "The flap opens on the straight and the car pulls clear before braking for the corner."
    return v


def pack(channel_ids, **overrides):
    p = {
        "client_ref": str(uuid.uuid4()), "niche": "f1", "template_key": "motorsport_tech_explainer", "clips": 2,
        "topic": f"Active aero rear wing {uuid.uuid4().hex[:6]}",
        "research_notes": "1. The 2026 rules let the rear wing flap move on straights. [S1][S2]",
        "sources": [
            {"id": "S1", "url": "https://www.fia.com/regulation", "publisher": "FIA", "title": "Technical regulations", "published_at": "2025-06-10"},
            {"id": "S2", "url": "https://www.the-race.com/x", "publisher": "The Race", "title": "Explained", "published_at": None},
        ],
        "references": [{"url": "https://www.instagram.com/reel/abc/", "platform": "instagram", "kind": "video_style",
                        "views": 120000, "views_approx": True, "likes": 5000, "comments": 80}],
        "score": 82, "rationale": "Reference reels on wings beat the account median by 3x.",
        "qc": {"passed": True, "facts_two_sources": True, "season_checked": "2026", "no_real_names": True,
               "motion_checked": True, "motion_notes": "Only the upper flap moves; main plane and endplates stay fixed."},
        "variants": [],
    }
    narrations = [NARRATION, NARRATION_B]
    for i, cid in enumerate(channel_ids):
        p["variants"].append({
            "channel_id": cid, "angle": f"angle {i}", "variables": variables(narration=narrations[i % 2]),
            "title": f"How the new rear wing works {i}", "caption": f"The rear wing flap now moves on every straight. #F1 #tech{i}",
        })
    p.update(overrides)
    return p


# ── Real-name guard ──────────────────────────────────────────────────────────

def test_real_names_hits_and_ordinary_words():
    assert find_real_names("Ferrari tried this at Monza, said Verstappen") == ["Ferrari", "Monza", "Verstappen"]
    assert find_real_names("a carbon shell, in March, glowing arrows, the alpine pass, a spa town") == []
    assert find_real_names("Red Bull and Joan Mir") == ["Red Bull", "Joan Mir"]


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_scopes(db, clean):
    client = TestClient(app)
    assert client.get("/research/brief").status_code in (401, 403)
    assert client.get("/research/brief", headers=token(db, ["read"])).status_code == 403
    assert client.get("/research/brief", headers=token(db, ["research"])).status_code == 200
    assert client.get("/research/brief", headers=token(db, ["labels:write"])).status_code == 200
    # A research token cannot read the Hermes endpoints, which carry page names.
    assert client.get("/hermes/fanpages", headers=token(db, ["research"])).status_code == 403
    assert client.get("/research/review/packs", headers=token(db, ["research"])).status_code == 403


# ── Brief ────────────────────────────────────────────────────────────────────

def test_brief_idle_cases(db, clean):
    client = TestClient(app)
    headers = token(db)
    body = client.get("/research/brief", headers=headers).json()
    assert body["action"] == "idle" and body["niches"] == [] and body["docs_version"]

    make_fanpage(db, clean)
    row = research_desk.get_settings_row(db)
    row.ai_video_researcher = "hermes"
    db.commit()
    assert client.get("/research/brief", headers=headers).json()["action"] == "idle"
    row.ai_video_researcher = "muse"
    db.commit()
    assert client.get("/research/brief", headers=headers).json()["action"] == "research"
    row.muse_pilot_packs = 0
    db.commit()
    assert client.get("/research/brief", headers=headers).json()["action"] == "idle"
    assert db.query(ResearchCall).filter(ResearchCall.endpoint == "brief").count() >= 4


def test_brief_research_shape_and_no_leak(db, clean):
    f1 = make_fanpage(db, clean)
    moto = make_fanpage(db, clean, name="Leaky MotoGP Page", niches=("motogp",))
    client = TestClient(app)
    body = client.get("/research/brief", headers=token(db)).json()
    assert body["action"] == "research" and body["mode"] == "pilot"
    niches = {n["niche"]: n for n in body["niches"]}
    assert niches["f1"]["packs_needed"] == 3 and niches["motogp"]["packs_needed"] == 3
    channel = niches["f1"]["channels"][0]
    assert channel["channel_id"] == f1.id and channel["ideas_needed"] == 3
    assert channel["templates"]["motorsport_tech_explainer"]["clip_options"] == [2, 3]
    assert channel["templates"]["motorsport_tech_explainer"]["narration_words"]["narration_1"] == {"target": "22-24", "accepted": "16-24"}
    assert niches["motogp"]["channels"][0]["templates"]["motorsport_tech_explainer"]["vehicle_presets"] == ["motogp_livery"]
    assert niches["f1"]["references"]["video_style"][0]["url"] == "https://www.instagram.com/formula.addict/"
    text = client.get("/research/brief", headers=token(db)).text.lower()
    for leak in (LEAK_NAME.lower(), "leaky motogp page", "fanpage", "facebook", "repliz", "see more", "/root/", "flow.sh", "channel_name"):
        assert leak not in text, leak
    docs = client.get("/research/docs", headers=token(db)).text.lower()
    for leak in ("fanpage", "facebook.com/", "repliz"):
        assert leak not in docs
    assert moto.id in [c["channel_id"] for c in niches["motogp"]["channels"]]


def test_schema_endpoint(db, clean):
    schema = TestClient(app).get("/research/schema", headers=token(db)).json()
    assert schema["$defs"]["pack"]["properties"]["variants"]["maxItems"] == 1


# ── Packs ────────────────────────────────────────────────────────────────────

def test_pack_created_idempotent_and_pilot_only(db, clean):
    a = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([a.id])
    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p], "run_report": {"sources_checked": 3}}).json()
    result = res["results"][0]
    assert result["status"] == "created", result
    stored = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).all()
    assert len(stored) == 1 and {i.status for i in stored} == {"pilot"} and {i.source for i in stored} == {"muse"}
    assert stored[0].title and stored[0].caption and stored[0].evidence["sources"]
    assert research_desk.get_settings_row(db).research_last_report["sources_checked"] == 3
    again = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()["results"][0]
    assert again["status"] == "exists" and len(again["variants"]) == 1
    listed = client.get(f"/research/packs?client_ref={p['client_ref']}", headers=headers).json()
    assert listed[0]["variants"][0]["status"] == "in_review"
    # Pilot ideas are invisible to the planner's queue and do not block production topics.
    assert db.query(VideoIdea).filter(VideoIdea.fanpage_id == a.id, VideoIdea.status.in_(["proposed", "approved"])).count() == 0
    ai_video_ideas.create_idea(db, a.id, {"template_key": "motorsport_tech_explainer", "topic": p["topic"]}, "owner", username="owner")


def test_pack_and_variant_rejections(db, clean):
    a, b = make_fanpage(db, clean), make_fanpage(db, clean, name="Second Page")
    client = TestClient(app)
    headers = token(db)

    bad = pack([a.id], score=50, sources=pack([a.id])["sources"][:1])
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [bad]}).json()["results"][0]
    assert r["status"] == "rejected" and any("sources" in e for e in r["errors"]) and any("score" in e for e in r["errors"])

    p = pack([a.id])
    p["variants"][0]["variables"]["narration_1"] = "too short line"
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()["results"][0]
    assert r["status"] == "rejected"
    assert "narration_1 must contain" in " ".join(r["variants"][0]["errors"])

    p_names = pack([b.id])
    p_names["variants"][0]["caption"] = "Ferrari found this first. #F1"
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p_names]}).json()["results"][0]
    assert r["status"] == "rejected"
    assert "real names" in " ".join(r["variants"][0]["errors"]) and "Ferrari" in " ".join(r["variants"][0]["errors"])

    p = pack([a.id])
    p["variants"][0]["variables"]["livery"] = "scarlet"
    p["variants"][0]["variables"]["vehicle_preset"] = "motogp"
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()["results"][0]
    errors = " ".join(r["variants"][0]["errors"])
    assert "do not send livery" in errors and "vehicle_preset must be one of" in errors

    p = pack([a.id])
    p["research_notes"] = "1. A fact. [S7]"
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]}).json()["results"][0]
    assert any("S7" in e for e in r["errors"])

    hook = pack([a.id])
    hook["variants"][0]["caption"] = ("This opening sentence keeps going and going well beyond what any viewer could possibly see "
                                      "before the feed cuts the text off, which makes it far too long for a hook line. #F1")
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [hook]}).json()["results"][0]
    assert "hook" in " ".join(r["variants"][0]["errors"])


def test_pilot_cap_and_topic_rules(db, clean):
    a = make_fanpage(db, clean)
    row = research_desk.get_settings_row(db)
    row.muse_pilot_packs = 1
    db.commit()
    client = TestClient(app)
    headers = token(db)
    first = pack([a.id])
    assert client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [first]}).json()["results"][0]["status"] == "created"
    assert client.get("/research/brief", headers=headers).json()["action"] == "idle"
    second = pack([a.id])
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [second]}).json()["results"][0]
    assert "needs no more ideas" in " ".join(r["variants"][0]["errors"])

    # Rejecting the pilot idea frees the slot; the same topic is still blocked for that channel.
    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == first["client_ref"]).one()
    db.query(VideoIdea).filter(VideoIdea.fanpage_id == a.id, VideoIdea.status != "rejected").count()
    ai_video_ideas.reject(db, idea, "owner")
    assert client.get("/research/brief", headers=headers).json()["action"] == "research"
    other = pack([a.id], topic=first["topic"])
    assert client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [other]}).json()["results"][0]["status"] == "created"
    row.muse_pilot_packs = 2
    db.commit()
    repeat = pack([a.id], topic=first["topic"])
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [repeat]}).json()["results"][0]
    assert "already had this topic" in " ".join(r["variants"][0]["errors"])


def test_saturated_topic(db, clean):
    pages = [make_fanpage(db, clean, name=f"Page {i}") for i in range(2)]
    topic = f"Active aero wing {uuid.uuid4().hex[:6]}"
    db.add(VideoIdea(fanpage_id=pages[0].id, template_key="motorsport_tech_explainer", topic=topic, source="hermes", status="approved"))
    db.commit()
    client = TestClient(app)
    headers = token(db)
    assert topic in client.get("/research/brief", headers=headers).json()["used_subjects"]
    r = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [pack([pages[1].id], topic=topic)]}).json()["results"][0]
    assert f"already used on channel {pages[0].id}" in " ".join(r["variants"][0]["errors"])


# ── Feedback both ways ───────────────────────────────────────────────────────

def test_feedback_round_trip(db, clean):
    a = make_fanpage(db, clean)
    client = TestClient(app)
    headers = token(db)
    p = pack([a.id])
    client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p]})
    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()

    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    research_desk.add_feedback(db, author="studio", kind="comment", message="Cite the season for every number.",
                               client_ref=p["client_ref"], idea_id=idea.id, username="owner")
    research_desk.reject_for_review(db, idea, "owner", "The second number has one source only.")
    items = client.get("/research/feedback", headers=headers, params={"since": before.isoformat()}).json()
    assert [i["kind"] for i in items] == ["rejected", "comment"]
    assert items[0]["channel_id"] == a.id and LEAK_NAME not in str(items)
    assert client.get("/research/brief", headers=headers).json()["latest_feedback_at"]

    note = client.post("/research/notes", headers=headers, json={"message": "The reference account went private."})
    assert note.status_code == 201
    thread = research_desk.feedback_thread(db, idea)
    assert [t["kind"] for t in thread] == ["comment", "rejected"]


def test_reviewer_endpoints(db, clean):
    a = make_fanpage(db, clean)
    client = TestClient(app)
    p = pack([a.id])
    client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]})
    reviewer = token(db, ["research:review"])
    packs = client.get("/research/review/packs", headers=reviewer, params={"status": "pilot"}).json()
    mine = next(x for x in packs if x["client_ref"] == p["client_ref"])
    assert mine["research_notes"] and mine["variants"][0]["variables"]["narration_1"]
    assert LEAK_NAME not in str(packs)
    res = client.post("/research/review/feedback", headers=reviewer,
                      json={"client_ref": p["client_ref"], "message": "Wrong pivot axis.", "reject": True}).json()
    assert len(res["rejected"]) == 1
    assert db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one().status == "rejected"


def test_admin_feedback_and_reject_reason(db, clean, monkeypatch):
    from app.api.deps import get_current_user

    a = make_fanpage(db, clean)
    client = TestClient(app)
    p = pack([a.id])
    client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]})
    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()
    app.dependency_overrides[get_current_user] = lambda: "owner"
    try:
        thread = client.post(f"/ai-video/ideas/{idea.id}/feedback", json={"message": "Good hook."}).json()
        assert thread[-1]["message"] == "Good hook."
        rejected = client.post(f"/ai-video/ideas/{idea.id}/reject", json={"reason": "Outdated rule."})
        assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
        assert [t["kind"] for t in client.get(f"/ai-video/ideas/{idea.id}/feedback").json()] == ["comment", "rejected"]
        listed = client.get("/ai-video/ideas", params={"fanpage_id": a.id}).json()
        assert listed[0]["title"] and listed[0]["client_ref"] == p["client_ref"]
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_settings_round_trip(db, clean):
    from app.api.deps import get_current_user

    client = TestClient(app)
    app.dependency_overrides[get_current_user] = lambda: "owner"
    try:
        out = client.get("/settings").json()
        assert out["ai_video_researcher"] == "muse" and out["muse_research_mode"] == "pilot"
        assert out["research_references"]["f1"]["topic_signals"]
        bad = client.put("/settings", json={"research_references": {"f1": {"video_style": ["http://insecure"]}}})
        assert bad.status_code == 400
        saved = client.put("/settings", json={"ai_video_researcher": "hermes", "muse_pilot_packs": 2,
                                              "research_references": {"f1": {"video_style": ["https://www.instagram.com/x/"]}}}).json()
        assert saved["ai_video_researcher"] == "hermes" and saved["muse_pilot_packs"] == 2
        assert saved["research_references"]["f1"]["video_style"] == [{"url": "https://www.instagram.com/x/"}]
        assert saved["research_references"]["motogp"] == {"video_style": [], "topic_signals": []}
        activity = client.get("/settings/research-activity").json()
        assert "calls" in activity and "notes" in activity
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── Planner uses Muse's own slots ────────────────────────────────────────────

def test_planner_uses_research_slots_without_llm(db, clean, monkeypatch):
    import app.tasks.ai_video_plan as planner

    a = make_fanpage(db, clean, ai_video_templates=["motorsport_tech_explainer"])
    a.ai_video_daily_count = 1
    db.commit()
    client = TestClient(app)
    p = pack([a.id])
    client.post("/research/packs", headers=token(db), json={"brief_id": "x", "packs": [p]})
    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()
    ai_video_ideas.approve(db, idea, "owner")
    monkeypatch.setattr(planner, "SessionLocal", lambda: db)
    with patch("app.services.news_copywriter._generate_with_fallback", side_effect=AssertionError("LLM must not be called")):
        planner.plan_ai_videos.run()
    idea = db.query(VideoIdea).filter(VideoIdea.client_ref == p["client_ref"]).one()
    assert idea.status == "queued", idea.error
    from app.models.ai_video import VideoJob
    job = db.query(VideoJob).filter(VideoJob.idea_id == idea.id).one()
    assert job.title == p["variants"][0]["title"] and job.caption == p["variants"][0]["caption"]
    assert NARRATION[1] in job.clips[0]["prompt"]
