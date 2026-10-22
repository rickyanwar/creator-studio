"""Tests for real_names_allowed template support in Muse pack validation."""
import uuid

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.research import ResearchFeedback
from app.models.ai_video import VideoIdea
from app.models.target_fanpages import TargetFanpage
from app.services import api_tokens, research_desk
from app.services.real_names import (
    CIRCUIT_NAMES,
    MAKER_TEAM_SPONSOR_NAMES,
    PERSON_NAMES,
    REAL_NAMES,
    find_person_names,
    find_real_names,
)


# ── Unit tests ───────────────────────────────────────────────────────────────


def test_unit_find_person_names_and_find_real_names():
    text = "Ducati Lenovo Team, ridden by Bagnaia"
    assert find_person_names(text) == ["Bagnaia"]
    assert find_real_names(text) == ["Ducati", "Lenovo", "Bagnaia"]


def test_unit_real_names_partition_no_loss_no_overlap():
    combined = MAKER_TEAM_SPONSOR_NAMES + PERSON_NAMES + CIRCUIT_NAMES
    assert REAL_NAMES == combined
    assert len(REAL_NAMES) == len(set(REAL_NAMES))
    maker_set = set(MAKER_TEAM_SPONSOR_NAMES)
    person_set = set(PERSON_NAMES)
    circuit_set = set(CIRCUIT_NAMES)
    assert maker_set.isdisjoint(person_set)
    assert maker_set.isdisjoint(circuit_set)
    assert person_set.isdisjoint(circuit_set)
    for name in REAL_NAMES:
        in_tuples = [name in maker_set, name in person_set, name in circuit_set]
        assert sum(in_tuples) == 1


# ── Integration tests (Postgres) ─────────────────────────────────────────────


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


def make_fanpage(db, created, name="MotoGP Page", niches=("motogp",), **kw):
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


def tech_variables(clips=2, narration=NARRATION, preset="f1"):
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
    return v


def motorcycle_commercial_variables():
    return {
        "vehicle_preset": "classic_bike",
        "bike_look": "a Ducati superbike in red Lenovo racing livery with Suzuki sponsor stickers on the fairing and plain number boards",
        "bike_short": "red factory racing superbike with sponsor lettering and plain number boards",
        "setting": "an old brick workshop with its roller door half open to warm morning light",
        "detail_1": "the red fuel tank and its white sponsor lettering",
        "detail_2": "the spoked front wheel and its single disc brake",
        "detail_3": "the four cooling-finned cylinders and the four chrome exhaust pipes",
        "ride_scene": "A sunlit coastal road curves along the cliffs, the sea glittering below.",
        "signature_move": "It sweeps through a long fast bend at a steady lean, all four exhaust pipes singing as the road opens toward the sea.",
        "narration_1": "In nineteen sixty-nine, Ducati rolled out this racing machine, and the motorcycle world suddenly had a new word: superbike.",
        "narration_2": "Four cylinders, four chrome pipes and a front disc brake gave everyday riders smooth, reliable speed that only racers knew before.",
        "narration_3": "Every modern sport bike carries a little of its DNA. The machine did not follow the future. It started it.",
    }


def pack_commercial(channel_ids, **overrides):
    p = {
        "client_ref": str(uuid.uuid4()), "niche": "motogp", "template_key": "motorcycle_commercial", "clips": 3,
        "topic": f"Ducati superbike classic {uuid.uuid4().hex[:6]}",
        "research_notes": "1. The legendary racing motorcycle was introduced in 1969. [S1][S2]",
        "sources": [
            {"id": "S1", "url": "https://example.com/source1", "publisher": "Motorcycle News", "title": "History of Ducati", "published_at": "2025-06-10"},
            {"id": "S2", "url": "https://example.com/source2", "publisher": "Cycle World", "title": "Explained", "published_at": None},
        ],
        "references": [{"url": "https://www.instagram.com/reel/abc/", "platform": "instagram", "kind": "video_style",
                        "views": 120000, "views_approx": True, "likes": 5000, "comments": 80}],
        "score": 85, "rationale": "High performing reference content.",
        "qc": {"passed": True, "facts_two_sources": True, "season_checked": "classic", "no_real_names": False,
               "motion_checked": True, "motion_notes": "Only wheels and rider move naturally; no floating parts."},
        "variants": [],
    }
    for i, cid in enumerate(channel_ids):
        p["variants"].append({
            "channel_id": cid, "angle": f"commercial angle {i}", "variables": motorcycle_commercial_variables(),
            "title": "Ducati Lenovo racer", "caption": "The Ducati Lenovo team built this Suzuki powered machine. #MotoGP #racing",
        })
    p.update(overrides)
    return p


def pack_tech(channel_ids, **overrides):
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
    for i, cid in enumerate(channel_ids):
        p["variants"].append({
            "channel_id": cid, "angle": f"angle {i}", "variables": tech_variables(),
            "title": f"How the new rear wing works {i}", "caption": f"The rear wing flap now moves on every straight. #F1 #tech{i}",
        })
    p.update(overrides)
    return p


def test_motorcycle_commercial_pack_accepted_with_maker_team_sponsor(db, clean):
    fp = make_fanpage(db, clean, name="MotoGP Commercial Page", niches=("motogp",), ai_video_templates=["motorcycle_commercial"])
    client = TestClient(app)
    headers = token(db)

    p = pack_commercial([fp.id])
    res = client.post("/research/packs", headers=headers, json={"brief_id": "b1", "packs": [p]}).json()["results"][0]
    assert res["status"] == "created", f"Expected created, got {res}"
    assert res["errors"] == []


def test_motorcycle_commercial_pack_rejected_with_person_name(db, clean):
    fp = make_fanpage(db, clean, name="MotoGP Commercial Page", niches=("motogp",), ai_video_templates=["motorcycle_commercial"])
    client = TestClient(app)
    headers = token(db)

    p = pack_commercial([fp.id])
    p["variants"][0]["variables"]["narration_1"] = "In nineteen sixty-nine Bagnaia rolled out this racing machine, and the motorcycle world suddenly had a new word: superbike."
    res = client.post("/research/packs", headers=headers, json={"brief_id": "b1", "packs": [p]}).json()["results"][0]
    assert res["status"] == "rejected"
    errors = " ".join(res["variants"][0]["errors"])
    assert "rider, driver and other person names are not allowed in narration_1: Bagnaia" in errors


def test_motorsport_tech_explainer_rejected_with_maker_name(db, clean):
    fp = make_fanpage(db, clean, name="F1 Tech Page", niches=("f1",), ai_video_templates=["motorsport_tech_explainer"])
    client = TestClient(app)
    headers = token(db)

    p = pack_tech([fp.id])
    p["variants"][0]["variables"]["part"] = "Ducati rear wing"
    res = client.post("/research/packs", headers=headers, json={"brief_id": "b1", "packs": [p]}).json()["results"][0]
    assert res["status"] == "rejected"
    errors = " ".join(res["variants"][0]["errors"])
    assert "real names are not allowed in part: Ducati" in errors
