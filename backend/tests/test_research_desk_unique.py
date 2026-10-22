import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.ai_video import VideoIdea, VideoJob
from app.models.research import ResearchFeedback
from app.models.target_fanpages import TargetFanpage
import app.tasks.ai_video_plan as planner
from app.services import api_tokens, research_desk
from app.services.research_desk import _now, _narration_text


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
    others = db.query(TargetFanpage).filter(TargetFanpage.ai_video_enabled == True).all()  # noqa: E712
    for fp in others:
        fp.ai_video_enabled = False
    row = research_desk.get_settings_row(db)
    row.ai_video_researcher, row.muse_research_mode, row.muse_pilot_packs = "muse", "pilot", 10
    row.research_references, row.research_last_report = None, None
    db.commit()
    created = []
    yield created
    db.rollback()
    ids = [fp_id for fp_id in created]
    db.query(VideoJob).filter(VideoJob.fanpage_id.in_(ids)).delete(synchronize_session=False)
    db.query(ResearchFeedback).delete(synchronize_session=False)
    db.query(VideoIdea).filter(VideoIdea.fanpage_id.in_(ids)).delete(synchronize_session=False)
    db.query(TargetFanpage).filter(TargetFanpage.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def make_fanpage(db, created, name="Test Fanpage", niches=("f1",), **kw):
    fp = TargetFanpage(
        repliz_account_id=f"uniq-{uuid.uuid4().hex[:12]}", name=name, is_active=True, is_connected=True,
        ai_video_enabled=True, mode2_gallery_niches=list(niches), caption_language="en", target_country="GB",
        ai_video_clips=3, ai_video_clips_min=2, ai_video_min_score=70, ai_video_daily_count=2, **kw,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    created.append(fp.id)
    return fp


def token(db, scopes=("research",)):
    _, plaintext = api_tokens.create_token(db, f"uniq-{uuid.uuid4().hex[:6]}", list(scopes))
    return {"Authorization": f"Bearer {plaintext}"}


NARRATION = {
    1: "Engineers moved the hinge forward so the upper flap now tilts on every straight and closes again before the braking zone arrives.",
    2: "With the flap open the slot lets air slip through, drag falls away, and the car reaches its top speed far sooner than before.",
}


def variables():
    v = {
        "part": "rear wing", "vehicle_preset": "f1",
        "mechanism_details": "a slim actuator cylinder inside the central pillar and the upper flap hinge",
        "mechanism_action": "The actuator pushes and the upper flap pivots upward around its forward hinge while the main plane stays fixed.",
        "moving_part": "upper rear-wing flap",
        "fixed_parts": "the lower main plane and both endplates",
        "effect_visual": "Glowing white airflow lines rush through the open slot of the rear wing.",
        "parts_list": "the endplates, the main plane, the upper flap, the actuator",
        "final_state": "the rear wing closes its flap",
        "narration_1": NARRATION[1],
        "narration_2": NARRATION[2],
    }
    return v


def pack(channel_id, topic, **overrides):
    p = {
        "client_ref": str(uuid.uuid4()), "niche": "f1", "template_key": "motorsport_tech_explainer", "clips": 2,
        "topic": topic,
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
        "variants": [{
            "channel_id": channel_id, "angle": "Unique angle", "variables": variables(),
            "title": f"How active aero works on {channel_id}", "caption": "The rear wing flap now moves on every straight. #F1 #tech",
        }],
    }
    p.update(overrides)
    return p


# ── 1. Constants ─────────────────────────────────────────────────────────────

def test_constants_are_1_and_10(db, clean):
    assert research_desk.MAX_CHANNELS_PER_PACK == 1
    assert research_desk.MAX_PACKS_PER_REQUEST == 10
    schema = TestClient(app).get("/research/schema", headers=token(db)).json()
    assert schema["$defs"]["pack"]["properties"]["variants"]["maxItems"] == 1
    assert schema["properties"]["packs"]["maxItems"] == 10


# ── 2. Brief used_subjects and no saturated_topics ───────────────────────────

def test_brief_has_used_subjects_and_no_saturated_topics(db, clean):
    a = make_fanpage(db, clean, name="Channel A")
    b = make_fanpage(db, clean, name="Channel B")
    topic_a = "The clutch bite point: the paddle release that launches the car"
    topic_b = "2026 active aero: the rear-wing flap that pivots between Corner and Straight mode"
    topic_rej = "The brake bias dial: fine-tuning front to rear stopping balance"

    db.add(VideoIdea(fanpage_id=a.id, template_key="motorsport_tech_explainer", topic=topic_a, source="muse", status="approved", created_at=_now() - timedelta(days=2)))
    db.add(VideoIdea(fanpage_id=b.id, template_key="motorsport_tech_explainer", topic=topic_b, source="muse", status="proposed", created_at=_now() - timedelta(days=1)))
    db.add(VideoIdea(fanpage_id=a.id, template_key="motorsport_tech_explainer", topic=topic_rej, source="muse", status="rejected", created_at=_now() - timedelta(days=1)))
    db.commit()

    client = TestClient(app)
    brief = client.get("/research/brief", headers=token(db)).json()
    assert brief["action"] == "research"
    assert "used_subjects" in brief
    assert "saturated_topics" not in brief

    assert topic_b in brief["used_subjects"]
    assert topic_a in brief["used_subjects"]
    assert topic_rej not in brief["used_subjects"]
    # Newest first: topic_b (1 day ago) before topic_a (2 days ago)
    assert brief["used_subjects"].index(topic_b) < brief["used_subjects"].index(topic_a)


# ── 3. Similar subject rejected, dissimilar accepted ─────────────────────────

def test_similar_subject_rejected_dissimilar_accepted(db, clean):
    a = make_fanpage(db, clean, name="Channel A")
    b = make_fanpage(db, clean, name="Channel B")

    topic_a = "The tear-off visor strips: the driver peeling transparent layers…"
    db.add(VideoIdea(fanpage_id=a.id, template_key="motorsport_tech_explainer", topic=topic_a, source="muse", status="approved", created_at=_now() - timedelta(days=2)))
    db.commit()

    client = TestClient(app)
    headers = token(db)

    # Similar topic for channel B -> rejected
    topic_b_similar = "The tear-off strip: one pull and the visor is crystal clear"
    bad_pack = pack(b.id, topic_b_similar)
    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [bad_pack]}).json()
    r = res["results"][0]
    assert r["status"] == "rejected"
    error = " ".join(r["variants"][0]["errors"])
    assert f"subject already used on channel {a.id} in the last 30 days: {topic_a[:80]}" in error

    # Dissimilar topic for channel B -> accepted
    topic_b_diff = "The clutch bite point: the paddle release that launches the car"
    good_pack = pack(b.id, topic_b_diff)
    res_good = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [good_pack]}).json()
    assert res_good["results"][0]["status"] == "created"


# ── 4. Two variants with similar subjects in one request ──────────────────────

def test_two_variants_similar_subjects_in_one_request(db, clean):
    a = make_fanpage(db, clean, name="Channel A")
    b = make_fanpage(db, clean, name="Channel B")

    client = TestClient(app)
    headers = token(db)

    # Two packs in one request with similar topics
    topic_1 = "The turbocharger: why turbo lag is back in 2026"
    topic_2 = "The turbo: exhaust gases spin its turbine wheel over 125,000 rpm"
    p1 = pack(a.id, topic_1)
    p2 = pack(b.id, topic_2)

    res = client.post("/research/packs", headers=headers, json={"brief_id": "x", "packs": [p1, p2]}).json()
    results = res["results"]
    assert results[0]["status"] == "created"
    assert results[1]["status"] == "rejected"
    assert f"already used on channel {a.id}" in " ".join(results[1]["variants"][0]["errors"])

    # Also check _check_variant directly against accepted list
    template = research_desk.get_template("motorsport_tech_explainer")
    v2 = p2["variants"][0]
    v_errors, _ = research_desk._check_variant(
        db, v2, p2, template, None, need_left={b.id: 1},
        accepted=[(a.id, "narration text", "Title A", topic_1)],
        used_subjects_cache=[],
    )
    assert any(f"already used on channel {a.id}" in e for e in v_errors)


# ── 5. Plan guard: cleans approved backlog and skips duplicate job ────────────

def test_plan_guard_skips_and_rejects_duplicate_approved_idea(db, clean, monkeypatch):
    session = db
    a = make_fanpage(session, clean, name="Page A")
    b = make_fanpage(session, clean, name="Page B")

    # Page A has a VideoJob from yesterday
    topic_a = "The turbocharger: why turbo lag is back in 2026"
    idea_a = VideoIdea(fanpage_id=a.id, template_key="motorsport_tech_explainer", topic=topic_a, status="done", source="muse")
    session.add(idea_a)
    session.flush()

    job_a = VideoJob(
        fanpage_id=a.id, idea_id=idea_a.id, template_key="motorsport_tech_explainer",
        title="Turbocharger tech", caption="Turbo tech caption", model="veo2", aspect="9:16",
        clip_seconds=5, clips=[{"index": 1}], credits_per_clip=15, credits_estimate=45,
        status="done", created_at=_now() - timedelta(days=1),
    )
    session.add(job_a)

    # Page B has an approved idea similar to Page A's job
    topic_b_dup = "The turbo: exhaust gases spin its turbine wheel over 125,000 rpm"
    idea_b_dup = VideoIdea(
        fanpage_id=b.id, template_key="motorsport_tech_explainer", topic=topic_b_dup,
        status="approved", score=95, rationale="high score", source="muse", clips=2,
        variables=variables(),
    )
    session.add(idea_b_dup)

    # Page B also has an approved idea with a dissimilar topic
    topic_b_good = "The clutch bite point: the paddle release that launches the car"
    idea_b_good = VideoIdea(
        fanpage_id=b.id, template_key="motorsport_tech_explainer", topic=topic_b_good,
        status="approved", score=85, rationale="medium score", source="muse", clips=2,
        variables=variables(),
    )
    session.add(idea_b_good)
    session.commit()

    monkeypatch.setattr(planner, "SessionLocal", SessionLocal)

    fill_return = (variables(), "Good Clutch Title", "Good Clutch Caption", [{"index": 1, "prompt": "p"}], "mock")
    with patch.object(planner, "fill_from_research", return_value=fill_return), \
         patch.object(planner, "fill_idea", return_value=fill_return):
        planner.plan_ai_videos.run()

    # Duplicate idea was rejected and backlog cleaned
    verify = SessionLocal()
    idea_dup = verify.get(VideoIdea, idea_b_dup.id)
    assert idea_dup.status == "rejected"
    assert idea_dup.error == f"duplicate subject: already used on channel {a.id}"

    # Dissimilar idea was queued into a VideoJob
    idea_good = verify.get(VideoIdea, idea_b_good.id)
    assert idea_good.status == "queued"
    job_b = verify.query(VideoJob).filter(VideoJob.fanpage_id == b.id, VideoJob.idea_id == idea_b_good.id).one()
    assert job_b.title == "Good Clutch Title"
    verify.close()
