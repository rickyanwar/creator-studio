import json
from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.database import Base, SessionLocal, engine
from app.main import app
from app.models.target_fanpages import TargetFanpage
from app.schemas.fanpage import FanpageOut, FanpageUpdate
from app.services import api_tokens
from app.services.ai_video_idea_writer import parse_ideas
from app.services.video_templates import clip_choices, get_template, pick_clips


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


def test_clip_choices():
    tech = get_template("motorsport_tech_explainer")
    corner = get_template("motorsport_corner_masterclass")

    # min None/max 2 -> [2]
    fp1 = SimpleNamespace(ai_video_clips=2, ai_video_clips_min=None)
    assert clip_choices(fp1, tech) == [2]

    # min 2/max 3 on tech explainer -> [2, 3]
    fp2 = SimpleNamespace(ai_video_clips=3, ai_video_clips_min=2)
    assert clip_choices(fp2, tech) == [2, 3]

    # same on corner masterclass (options [2]) -> [2]
    assert clip_choices(fp2, corner) == [2]

    # max 5 (not an option) -> falls back to default -> [2]
    fp3 = SimpleNamespace(ai_video_clips=5, ai_video_clips_min=None)
    assert clip_choices(fp3, tech) == [2]

    # min 3/max 2 (swapped) -> [2, 3]
    fp4 = SimpleNamespace(ai_video_clips=2, ai_video_clips_min=3)
    assert clip_choices(fp4, tech) == [2, 3]


def test_pick_clips():
    tech = get_template("motorsport_tech_explainer")
    fp = SimpleNamespace(ai_video_clips=3, ai_video_clips_min=2)

    # idea 3 in [2, 3] -> 3
    assert pick_clips(3, fp, tech) == 3

    # idea None -> 3 (longest)
    assert pick_clips(None, fp, tech) == 3

    # idea 5 -> 3
    assert pick_clips(5, fp, tech) == 3

    # idea 1 -> 2
    assert pick_clips(1, fp, tech) == 2

    # range [2] with idea 3 -> 2
    fp_single = SimpleNamespace(ai_video_clips=2, ai_video_clips_min=None)
    assert pick_clips(3, fp_single, tech) == 2


def test_schema_fanpage_clips_range():
    # min 3 / max 2 raises
    with pytest.raises(ValidationError, match="ai_video_clips_min must be <= ai_video_clips"):
        FanpageUpdate(ai_video_clips=2, ai_video_clips_min=3)

    # min None ok
    up_none = FanpageUpdate(ai_video_clips=2, ai_video_clips_min=None)
    assert up_none.ai_video_clips_min is None

    # min 2 / max 3 ok
    up_ok = FanpageUpdate(ai_video_clips=3, ai_video_clips_min=2)
    assert up_ok.ai_video_clips == 3
    assert up_ok.ai_video_clips_min == 2

    # FanpageOut returns the new field
    assert "ai_video_clips_min" in FanpageOut.model_fields


def test_parse_ideas_clips_handling():
    allowed = {"motorsport_tech_explainer": [2, 3]}
    payload = json.dumps({
        "ideas": [
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Topic A",
                "angle": "Angle A",
                "clips": 3,
                "score": 85,
                "rationale": "Rationale A",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Topic B",
                "angle": "Angle B",
                "clips": 9,
                "score": 80,
                "rationale": "Rationale B",
            },
        ]
    })
    results = parse_ideas(payload, allowed_clips=allowed, count=2)
    assert len(results) == 2

    # keeps clips 3 when allowed [2, 3]
    assert results[0]["topic"] == "Topic A"
    assert results[0]["clips"] == 3

    # drops clips 9 but keeps the idea
    assert results[1]["topic"] == "Topic B"
    assert "clips" not in results[1]


def test_hermes_fanpages_clip_range(db_session):
    _, read_token = api_tokens.create_token(db_session, "hermes-clip-range-token", ["read"])
    fp = TargetFanpage(
        repliz_account_id=f"m8-range-{datetime.now().timestamp()}",
        name="Hermes Range FP",
        is_active=True,
        ai_video_enabled=True,
        ai_video_clips=3,
        ai_video_clips_min=2,
        ai_video_templates=["motorsport_tech_explainer"],
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)

    try:
        client = TestClient(app)
        res = client.get("/hermes/fanpages", headers={"Authorization": f"Bearer {read_token}"})
        assert res.status_code == 200
        item = next(i for i in res.json() if i["id"] == fp.id)
        assert item["ai_video_clips"] == 3
        assert item["ai_video_clips_min"] == 2
        assert item["ai_video_clip_choices"] == {"motorsport_tech_explainer": [2, 3]}
    finally:
        db_session.delete(fp)
        db_session.commit()
