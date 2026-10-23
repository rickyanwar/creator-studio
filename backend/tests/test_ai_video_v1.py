import pytest
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.database import Base, SessionLocal, engine
from app.models.ai_video import GflowAccount, GflowWorker, VideoIdea, VideoJob
from app.models.api_tokens import ApiToken
from app.models.publish_jobs import ContentType, PublishJob
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.schemas.fanpage import FanpageBase, FanpageOut, FanpageUpdate
from app.schemas.settings import SettingsOut, SettingsUpdate
from app.services import api_tokens
from app.services import video_templates


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_ai_video_template_registry():
    templates = video_templates.list_templates()
    assert len(templates) >= 1
    t = video_templates.get_template("motorsport_tech_explainer")
    assert t.key == "motorsport_tech_explainer"
    assert t.model == "omni-flash"
    assert t.aspect == "9:16"
    assert t.clip_seconds == 10
    assert t.credits_per_clip == 15
    assert t.clip_options == (1, 2, 3, 4, 5, 6)
    assert t.default_clips == 2
    assert "f1" in t.niches
    assert video_templates.credits_estimate(t, 2) == 30
    assert video_templates.credits_estimate(t, 3) == 45

    with pytest.raises(ValueError, match="unknown template"):
        video_templates.get_template("non_existent_template")


def test_ai_video_template_required_variables_and_presets():
    t = video_templates.get_template("motorsport_tech_explainer")
    vars_2 = video_templates.required_variables(t, 2)
    assert vars_2 == [
        "part",
        "mechanism_details",
        "mechanism_action",
        "moving_part",
        "fixed_parts",
        "narration_1",
        "effect_visual",
        "parts_list",
        "final_state",
        "narration_2",
        "vehicle_preset",
    ]

    vars_3 = video_templates.required_variables(t, 3)
    assert vars_3 == [
        "part",
        "mechanism_details",
        "mechanism_action",
        "moving_part",
        "fixed_parts",
        "narration_1",
        "effect_visual",
        "parts_list",
        "final_state",
        "narration_2",
        "payoff_action",
        "narration_3",
        "vehicle_preset",
    ]

    presets = video_templates.vehicle_presets(t)
    assert "f1_red" in presets
    assert "motogp" in presets
    assert not any(p.endswith("_short") for p in presets)


def _sample_variables_3():
    return {
        "vehicle_preset": "f1_red",
        "part": "rear wing",
        "mechanism_details": "the DRS mechanism inside central pillar",
        "mechanism_action": "The piston pushes and upper flap visibly rotates upward opening a wide slot between elements.",
        "moving_part": "upper rear-wing flap",
        "fixed_parts": "the lower main plane, both vertical endplates and the central support pillar",
        "effect_visual": "Glowing white airflow lines rush straight through the open slot of the rear wing.",
        "parts_list": "endplates, main plane, upper flap, hydraulic actuator, piston, gears",
        "final_state": "the complete rear wing closes its flap",
        # 22-24 words: lo-6 = 16 words. 18 words here:
        "narration_1": "This is DRS, the Drag Reduction System. Inside the rear wing, a small hydraulic actuator pushes a piston forward.",
        # 20 words here:
        "narration_2": "The open slot lets air slip straight through, cutting drag, so the car gains ten to twelve kilometres per hour.",
        # 18 words here:
        "narration_3": "That extra speed is why drivers chase the car ahead. Close enough on the straight, the overtake is on now.",
        "payoff_action": "The rear wing flap opens on the long straight and the car pulls alongside and overtakes rival.",
    }


def test_render_clips_happy_path_2_and_3():
    t = video_templates.get_template("motorsport_tech_explainer")
    v = _sample_variables_3()

    # 2 clips
    rendered_2 = video_templates.render_clips(t, v, 2)
    assert len(rendered_2) == 2
    assert rendered_2[0]["index"] == 1
    assert rendered_2[0]["mode"] == "t2v"
    assert "rear wing" in rendered_2[0]["prompt"]
    assert "{" not in rendered_2[0]["prompt"]
    assert "}" not in rendered_2[0]["prompt"]
    assert rendered_2[1]["index"] == 2
    assert rendered_2[1]["mode"] == "i2v"
    assert "{" not in rendered_2[1]["prompt"]

    # 3 clips
    rendered_3 = video_templates.render_clips(t, v, 3)
    assert len(rendered_3) == 3
    assert rendered_3[2]["index"] == 3
    assert rendered_3[2]["mode"] == "i2v"
    assert "{" not in rendered_3[2]["prompt"]


def test_render_clips_value_errors():
    t = video_templates.get_template("motorsport_tech_explainer")
    base = _sample_variables_3()

    # invalid clips count
    with pytest.raises(ValueError, match="clips must be one of"):
        video_templates.render_clips(t, base, 7)

    # missing variable
    v_missing = dict(base)
    del v_missing["part"]
    with pytest.raises(ValueError, match="required variable part must be a non-empty string"):
        video_templates.render_clips(t, v_missing, 2)

    # empty string
    v_empty = dict(base, part="   ")
    with pytest.raises(ValueError, match="required variable part must be a non-empty string"):
        video_templates.render_clips(t, v_empty, 2)

    # unknown vehicle_preset
    v_preset = dict(base, vehicle_preset="nascar")
    with pytest.raises(ValueError, match="unknown vehicle_preset"):
        video_templates.render_clips(t, v_preset, 2)

    # longer than 600 chars
    v_long = dict(base, part="x" * 601)
    with pytest.raises(ValueError, match="invalid value for part: length or braces"):
        video_templates.render_clips(t, v_long, 2)

    # braces inside value
    v_brace = dict(base, part="rear {wing}")
    with pytest.raises(ValueError, match="invalid value for part: length or braces"):
        video_templates.render_clips(t, v_brace, 2)

    # double quotes in narration
    v_quote = dict(base, narration_1='Look at "this" wing now in action on circuit')
    with pytest.raises(ValueError, match="narration_1 must not contain a double quote"):
        video_templates.render_clips(t, v_quote, 2)

    # narration word budget too short (< lo - 6 = 16)
    v_short_narration = dict(base, narration_1="This is DRS system.")
    with pytest.raises(ValueError, match="narration_1 must contain 16–24 words"):
        video_templates.render_clips(t, v_short_narration, 2)

    # narration word budget too long (> hi = 24)
    v_long_narration = dict(base, narration_1="word " * 25)
    with pytest.raises(ValueError, match="narration_1 must contain 16–24 words"):
        video_templates.render_clips(t, v_long_narration, 2)


def test_api_token_new_scopes(db_session: Session):
    assert "ideas:write" in api_tokens.VALID_SCOPES
    assert "gflow:worker" in api_tokens.VALID_SCOPES

    token_db, plaintext = api_tokens.create_token(
        db_session, "Worker Token", ["ideas:write", "gflow:worker"]
    )
    assert token_db.scopes == ["ideas:write", "gflow:worker"]
    verified = api_tokens.verify(db_session, plaintext)
    assert verified is not None
    assert verified.id == token_db.id

    db_session.delete(token_db)
    db_session.commit()


def test_fanpage_schemas_ai_video():
    f = FanpageBase()
    assert f.ai_video_enabled is False
    assert f.ai_video_publish_mode == PublishMode.manual_review
    assert f.ai_video_daily_count == 1
    assert f.ai_video_min_score == 70
    assert f.ai_video_template is None
    assert f.ai_video_clips == 2

    # template validation
    valid_fp = FanpageUpdate(ai_video_template="motorsport_tech_explainer")
    assert valid_fp.ai_video_template == "motorsport_tech_explainer"

    with pytest.raises(ValidationError):
        FanpageUpdate(ai_video_template="unknown_key_xyz")

    with pytest.raises(ValidationError):
        FanpageUpdate(ai_video_clips=7)


def test_settings_schemas_ai_video():
    s_update = SettingsUpdate(
        ai_video_worker_enabled=False,
        ai_video_vision_qc_enabled=True,
        ai_video_credit_reserve=150,
    )
    assert s_update.ai_video_worker_enabled is False
    assert s_update.ai_video_vision_qc_enabled is True
    assert s_update.ai_video_credit_reserve == 150

    with pytest.raises(ValidationError):
        SettingsUpdate(ai_video_credit_reserve=10001)


def test_models_round_trip(db_session: Session):
    # TargetFanpage
    fanpage = TargetFanpage(
        repliz_account_id="fp_v1_test_acc",
        name="V1 Test Fanpage",
        ai_video_enabled=True,
        ai_video_publish_mode=PublishMode.auto,
        ai_video_daily_count=3,
        ai_video_min_score=80,
        ai_video_template="motorsport_tech_explainer",
        ai_video_clips=2,
    )
    db_session.add(fanpage)
    db_session.commit()
    db_session.refresh(fanpage)

    # Worker
    worker = GflowWorker(
        worker_id="mac-mini-01",
        version="0.83",
        max_concurrent=1,
        mem_available_mb=4096,
        running_job_ids=[1, 2],
    )
    db_session.add(worker)
    db_session.commit()

    # Account
    account = GflowAccount(
        worker_id="mac-mini-01",
        profile="profile1",
        email="test@gmail.com",
        credits=150,
        status="ok",
    )
    db_session.add(account)
    db_session.commit()
    db_session.refresh(account)

    # Idea
    idea = VideoIdea(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic="DRS rear wing",
        status="proposed",
        score=85,
        source="hermes",
    )
    db_session.add(idea)
    db_session.commit()
    db_session.refresh(idea)

    # Job
    job = VideoJob(
        idea_id=idea.id,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="DRS Explainer",
        caption="How DRS works",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1, "mode": "t2v", "prompt": "prompt 1"}],
        credits_per_clip=15,
        credits_estimate=30,
        account_id=account.id,
        worker_id="mac-mini-01",
        status="queued",
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    # ContentType enum check
    assert ContentType.ai_video == "ai_video"

    # Settings columns check
    settings_row = db_session.query(Settings).filter_by(id=1).first()
    if not settings_row:
        settings_row = Settings(id=1)
        db_session.add(settings_row)
        db_session.commit()
    assert settings_row.ai_video_worker_enabled is True
    assert settings_row.ai_video_vision_qc_enabled is False
    assert settings_row.ai_video_credit_reserve == 0

    # Clean up test rows
    db_session.delete(job)
    db_session.delete(idea)
    db_session.delete(account)
    db_session.delete(worker)
    db_session.delete(fanpage)
    db_session.commit()
