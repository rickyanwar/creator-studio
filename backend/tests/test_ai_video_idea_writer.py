import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine as _create_engine, text as _text, DefaultClause as _DefaultClause
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler as _SQLiteTypeCompiler
from sqlalchemy.pool import StaticPool as _StaticPool
from sqlalchemy.orm import sessionmaker as _sessionmaker

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea
from app.models.target_fanpages import TargetFanpage

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
_SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

try:
    with engine.connect():
        pass
    _USE_SQLITE = False
except Exception:
    _USE_SQLITE = True

if _USE_SQLITE:
    from app.models.social_publishing import SocialDestination
    from app.models.settings import Settings

    for _col_name in ["mode2_gallery_keywords", "mode2_gallery_niches", "radar_niches", "caption_must_include", "caption_must_avoid"]:
        _col = TargetFanpage.__table__.columns.get(_col_name)
        if _col is not None:
            _col.server_default = _DefaultClause(_text("''"))

    _col = SocialDestination.__table__.columns.get("supported_media")
    if _col is not None:
        _col.server_default = None

    _test_engine = _create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=_StaticPool,
    )
    for _tbl in [TargetFanpage.__table__, VideoIdea.__table__, Settings.__table__, SocialDestination.__table__]:
        _tbl.create(bind=_test_engine)

    SessionLocal = _sessionmaker(bind=_test_engine)


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    if not _USE_SQLITE:
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
        repliz_account_id=f"m8w-{id(db_session)}-{datetime.now().timestamp()}",
        name="MotoGP Page",
        is_active=True,
        is_connected=True,
        timezone="UTC",
        mode2_gallery_niches=["motogp"],
        ai_video_enabled=True,
        ai_video_daily_count=2,
        ai_video_template="motorsport_tech_explainer",
    )
    db_session.add(fp)
    db_session.commit()
    db_session.refresh(fp)
    yield fp
    verify = SessionLocal()
    verify.query(VideoIdea).filter(VideoIdea.fanpage_id == fp.id).delete()
    verify.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete()
    verify.commit()
    verify.close()


def test_parse_ideas_filtering_and_capping():
    from app.services.ai_video_idea_writer import parse_ideas

    payload = json.dumps({
        "ideas": [
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Valid Topic One",
                "angle": "Angle 1",
                "score": 85,
                "rationale": "Valid rationale 1",
            },
            {
                "template_key": "unknown_key",
                "topic": "Valid Topic Two",
                "score": 90,
                "rationale": "Valid rationale 2",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Valid Topic Three",
                "score": True,
                "rationale": "Valid rationale 3",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Valid Topic Four",
                "score": 105,
                "rationale": "Valid rationale 4",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Valid Topic Five",
                "score": 75,
                "rationale": "",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "No",
                "score": 75,
                "rationale": "Valid rationale",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Valid Topic Six",
                "angle": "Angle 6",
                "score": 92,
                "rationale": "Valid rationale 6",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Valid Topic Seven",
                "score": 88,
                "rationale": "Valid rationale 7",
            },
        ]
    })

    result = parse_ideas(payload, allowed_clips={"motorsport_tech_explainer": [2, 3]}, count=2)
    assert len(result) == 2
    assert result[0]["topic"] == "Valid Topic One"
    assert result[0]["score"] == 85
    assert result[1]["topic"] == "Valid Topic Six"
    assert result[1]["score"] == 92


def test_parse_ideas_fences_and_errors():
    from app.services.ai_video_idea_writer import parse_ideas

    fenced = """```json
    {
      "ideas": [
        {
          "template_key": "motorsport_tech_explainer",
          "topic": "Fenced Idea Topic",
          "angle": "Fenced Angle",
          "score": 80,
          "rationale": "Fenced Rationale"
        }
      ]
    }
    ```"""
    res = parse_ideas(fenced, allowed_clips={"motorsport_tech_explainer": [2, 3]}, count=1)
    assert len(res) == 1
    assert res[0]["topic"] == "Fenced Idea Topic"

    with pytest.raises(ValueError, match="invalid JSON"):
        parse_ideas("not a valid json at all", allowed_clips={"motorsport_tech_explainer": [2, 3]}, count=1)

    with pytest.raises(ValueError, match="missing ideas list"):
        parse_ideas('{"something_else": []}', allowed_clips={"motorsport_tech_explainer": [2, 3]}, count=1)

    with pytest.raises(ValueError, match="no valid ideas"):
        parse_ideas(
            json.dumps({"ideas": [{"template_key": "wrong_key", "topic": "Valid Topic", "score": 80, "rationale": "R"}]}),
            allowed_clips={"motorsport_tech_explainer": [2, 3]},
            count=1,
        )


def test_topic_ideas_formats():
    from app.services.ai_video_idea_writer import _topic_ideas

    t_dict = SimpleNamespace(raw={"topic_ideas": {"motogp": ["MotoGP topic"], "f1": ["F1 topic"]}})
    assert _topic_ideas(t_dict, "motogp") == ["MotoGP topic"]
    assert _topic_ideas(t_dict, "rally") == []

    t_list = SimpleNamespace(raw={"topic_ideas": ["General topic 1", "General topic 2"]})
    assert _topic_ideas(t_list, "motogp") == ["General topic 1", "General topic 2"]

    t_missing = SimpleNamespace(raw={})
    assert _topic_ideas(t_missing, "motogp") == []
    t_none = SimpleNamespace(raw={"topic_ideas": None})
    assert _topic_ideas(t_none, "motogp") == []


def test_writer_templates_resolution(fanpage):
    from app.services.ai_video_idea_writer import writer_templates

    fanpage.ai_video_template = "motorsport_tech_explainer"
    fanpage.ai_video_templates = None
    templates = writer_templates(fanpage)
    assert [t.key for t in templates] == ["motorsport_tech_explainer"]

    fanpage.ai_video_template = None
    fanpage.ai_video_templates = None
    fanpage.mode2_gallery_niches = ["motogp"]
    templates = writer_templates(fanpage)
    assert len(templates) > 0
    assert all("motogp" in t.niches for t in templates)


def test_build_idea_prompt_contents(fanpage):
    from app.services.ai_video_idea_writer import build_idea_prompt
    from app.services.video_templates import get_template

    avoid = ["Brembo carbon rotors", "Ride height hydraulic valve"]
    templates = [get_template("motorsport_tech_explainer")]
    prompt = build_idea_prompt(fanpage, templates, avoid, count=3)

    for item in avoid:
        assert item in prompt
    assert "motorsport_tech_explainer" in prompt
    assert "strict JSON" in prompt


def test_write_ideas_creation_and_conflict_handling(db_session, fanpage, monkeypatch):
    from app.services import ai_video_idea_writer

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()

    def fake_generate(prompt, parse_fn, context=None, fanpage_id=None):
        raw_json = json.dumps({
            "ideas": [
                {
                    "template_key": "motorsport_tech_explainer",
                    "topic": "Desmodromic valve timing",
                    "angle": "How mechanical closing prevents valve float",
                    "score": 85,
                    "rationale": "High audience interest in engine tech",
                },
                {
                    "template_key": "motorsport_tech_explainer",
                    "topic": "Desmodromic valve timing",
                    "angle": "Duplicate angle",
                    "score": 80,
                    "rationale": "Duplicate rationale",
                },
                {
                    "template_key": "motorsport_tech_explainer",
                    "topic": "Seamless shift gearbox",
                    "angle": "Instantaneous gear changes under full throttle",
                    "score": 90,
                    "rationale": "Great technical animation subject",
                },
            ]
        })
        return parse_fn(raw_json), "fake_provider"

    monkeypatch.setattr(ai_video_idea_writer.news_copywriter, "_generate_with_fallback", fake_generate)

    created_count = ai_video_idea_writer.write_ideas(db_session, fanpage, 3)
    assert created_count == 2

    ideas = (
        db_session.query(VideoIdea)
        .filter(VideoIdea.fanpage_id == fanpage.id)
        .order_by(VideoIdea.created_at.asc())
        .all()
    )
    assert len(ideas) == 2
    for idea in ideas:
        assert idea.source == "ai"
        assert idea.status == "proposed"
    assert {i.topic for i in ideas} == {"Desmodromic valve timing", "Seamless shift gearbox"}

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()


def test_top_up_ai_video_ideas_task(fanpage, monkeypatch):
    import app.tasks.ai_video_plan as planner
    from app.models.settings import Settings

    s_db = SessionLocal()
    settings_row = s_db.query(Settings).first()
    if settings_row is None:
        settings_row = Settings()
        s_db.add(settings_row)
        s_db.commit()
        s_db.refresh(settings_row)
    orig_researcher = settings_row.ai_video_researcher
    orig_bank_days = settings_row.ai_video_idea_bank_days
    settings_row.ai_video_researcher = "hermes"
    settings_row.ai_video_idea_bank_days = 1
    s_db.commit()
    s_db.close()

    try:
        monkeypatch.setattr(planner, "SessionLocal", SessionLocal)

        calls = []

        def fake_write(db, fp, need):
            calls.append((fp.id, need))
            return need

        monkeypatch.setattr(planner, "write_ideas", fake_write)

        verify = SessionLocal()
        try:
            verify.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
            verify.commit()

            # 7a: queue already has 2 waiting ideas -> writer NOT called
            idea1 = VideoIdea(
                fanpage_id=fanpage.id,
                template_key="motorsport_tech_explainer",
                topic="Waiting idea 1",
                status="proposed",
                source="ai",
                score=80,
                rationale="R1",
            )
            idea2 = VideoIdea(
                fanpage_id=fanpage.id,
                template_key="motorsport_tech_explainer",
                topic="Waiting idea 2",
                status="approved",
                source="ai",
                score=85,
                rationale="R2",
            )
            verify.add_all([idea1, idea2])
            verify.commit()

            calls.clear()
            planner.top_up_ai_video_ideas.run()
            assert not any(fp_id == fanpage.id for fp_id, _ in calls)

            # 7b: empty queue -> writer called with need == 2
            verify.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
            verify.commit()

            calls.clear()
            planner.top_up_ai_video_ideas.run()
            fixture_calls = [need for fp_id, need in calls if fp_id == fanpage.id]
            assert fixture_calls == [2]

            # 7c: 4 source="ai" ideas created in the last 24 h (status "done") -> writer NOT called (cap)
            verify.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            done_ideas = [
                VideoIdea(
                    fanpage_id=fanpage.id,
                    template_key="motorsport_tech_explainer",
                    topic=f"Done idea {i}",
                    status="done",
                    source="ai",
                    score=80 + i,
                    rationale=f"Rationale {i}",
                    created_at=now - timedelta(hours=i),
                )
                for i in range(4)
            ]
            verify.add_all(done_ideas)
            verify.commit()

            calls.clear()
            planner.top_up_ai_video_ideas.run()
            assert not any(fp_id == fanpage.id for fp_id, _ in calls)

            # 7d: writer raises RuntimeError -> task does not raise
            verify.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
            verify.commit()

            def broken_write(db, fp, need):
                if fp.id == fanpage.id:
                    raise RuntimeError("LLM service unavailable")
                return 0

            monkeypatch.setattr(planner, "write_ideas", broken_write)
            planner.top_up_ai_video_ideas.run()

        finally:
            verify.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
            verify.commit()
            verify.close()
    finally:
        s_db = SessionLocal()
        try:
            r = s_db.query(Settings).first()
            if r:
                r.ai_video_researcher = orig_researcher
                r.ai_video_idea_bank_days = orig_bank_days
                s_db.commit()
        finally:
            s_db.close()


def test_create_idea_ai_source_requires_score_and_rationale(db_session, fanpage):
    from app.services.ai_video_ideas import create_idea

    data_no_score = {
        "template_key": "motorsport_tech_explainer",
        "topic": "Test Score Requirement",
        "rationale": "Good rationale",
    }
    with pytest.raises(ValueError, match="Hermes and AI ideas require score and rationale"):
        create_idea(db_session, fanpage.id, data_no_score, source="ai")

    data_no_rationale = {
        "template_key": "motorsport_tech_explainer",
        "topic": "Test Rationale Requirement",
        "score": 85,
    }
    with pytest.raises(ValueError, match="Hermes and AI ideas require score and rationale"):
        create_idea(db_session, fanpage.id, data_no_rationale, source="ai")


def test_write_ideas_uses_chatgpt_first(db_session, fanpage, monkeypatch):
    from app.services import ai_video_idea_writer

    cfg = SimpleNamespace(enabled=True, base_url="http://test-gptcli:8099", token="secret_tok")
    monkeypatch.setattr(ai_video_idea_writer, "get_gptcli_config", lambda db: cfg)
    monkeypatch.setattr(ai_video_idea_writer, "gptcli_unavailable_until", lambda: None)

    gen_calls = []
    fake_reply = json.dumps({
        "ideas": [
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Active suspension roll control",
                "angle": "How hydraulic actuators level the chassis in corners",
                "score": 88,
                "rationale": "Strong visual physics concept",
            },
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Blown diffuser aerodynamics",
                "angle": "Exhaust gases energising floor downforce",
                "score": 92,
                "rationale": "Classic motorsport aero innovation",
            },
        ]
    })

    def fake_generate_text(c, p, *, request_id=None, max_wait_s=None):
        gen_calls.append({"cfg": c, "prompt": p, "request_id": request_id, "max_wait_s": max_wait_s})
        return SimpleNamespace(text=fake_reply, account="acc1", seconds=12.5)

    monkeypatch.setattr(ai_video_idea_writer, "generate_text", fake_generate_text)

    fallback_called = []

    def fake_fallback(prompt, parse_fn, context=None, fanpage_id=None):
        fallback_called.append(True)
        return [], "fallback"

    monkeypatch.setattr(ai_video_idea_writer.news_copywriter, "_generate_with_fallback", fake_fallback)

    created_count = ai_video_idea_writer.write_ideas(db_session, fanpage, 2)
    assert created_count == 2
    assert len(fallback_called) == 0
    assert len(gen_calls) == 1
    assert gen_calls[0]["prompt"].startswith(ai_video_idea_writer.CHATGPT_IDEA_PREFIX)
    assert gen_calls[0]["max_wait_s"] == 300

    ideas = (
        db_session.query(VideoIdea)
        .filter(VideoIdea.fanpage_id == fanpage.id)
        .order_by(VideoIdea.created_at.asc())
        .all()
    )
    assert len(ideas) == 2
    assert {i.topic for i in ideas} == {"Active suspension roll control", "Blown diffuser aerodynamics"}

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()


def test_write_ideas_falls_back_when_chatgpt_errors(db_session, fanpage, monkeypatch):
    from app.services import ai_video_idea_writer
    from app.services.prompt_polish import PolishUnavailable

    cfg = SimpleNamespace(enabled=True, base_url="http://test-gptcli:8099", token="secret_tok")
    monkeypatch.setattr(ai_video_idea_writer, "get_gptcli_config", lambda db: cfg)
    monkeypatch.setattr(ai_video_idea_writer, "gptcli_unavailable_until", lambda: None)

    def broken_generate(*a, **kw):
        raise PolishUnavailable("upstream", "server 500")

    monkeypatch.setattr(ai_video_idea_writer, "generate_text", broken_generate)

    fallback_calls = []

    def fake_fallback(prompt, parse_fn, context=None, fanpage_id=None):
        fallback_calls.append(True)
        return [
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Fallback Topic From 9Router",
                "angle": "Fallback Angle",
                "score": 85,
                "rationale": "Fallback Rationale",
            }
        ], "nine_router"

    monkeypatch.setattr(ai_video_idea_writer.news_copywriter, "_generate_with_fallback", fake_fallback)

    created_count = ai_video_idea_writer.write_ideas(db_session, fanpage, 1)
    assert created_count == 1
    assert len(fallback_calls) == 1

    ideas = (
        db_session.query(VideoIdea)
        .filter(VideoIdea.fanpage_id == fanpage.id)
        .all()
    )
    assert any(i.topic == "Fallback Topic From 9Router" for i in ideas)

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()


def test_write_ideas_falls_back_on_unparsable_chatgpt_reply(db_session, fanpage, monkeypatch):
    from app.services import ai_video_idea_writer

    cfg = SimpleNamespace(enabled=True, base_url="http://test-gptcli:8099", token="secret_tok")
    monkeypatch.setattr(ai_video_idea_writer, "get_gptcli_config", lambda db: cfg)
    monkeypatch.setattr(ai_video_idea_writer, "gptcli_unavailable_until", lambda: None)
    monkeypatch.setattr(
        ai_video_idea_writer,
        "generate_text",
        lambda *a, **kw: SimpleNamespace(text="sorry, I can't", account="acc1", seconds=1.0),
    )

    fallback_calls = []

    def fake_fallback(prompt, parse_fn, context=None, fanpage_id=None):
        fallback_calls.append(True)
        return [
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Unparsable Fallback Topic",
                "angle": "Angle",
                "score": 87,
                "rationale": "Rationale",
            }
        ], "nine_router"

    monkeypatch.setattr(ai_video_idea_writer.news_copywriter, "_generate_with_fallback", fake_fallback)

    created_count = ai_video_idea_writer.write_ideas(db_session, fanpage, 1)
    assert created_count == 1
    assert len(fallback_calls) == 1

    ideas = db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).all()
    assert any(i.topic == "Unparsable Fallback Topic" for i in ideas)

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()


def test_write_ideas_skips_chatgpt_when_resting_or_unconfigured(db_session, fanpage, monkeypatch):
    from app.services import ai_video_idea_writer

    # (a) resting
    cfg = SimpleNamespace(enabled=True, base_url="http://test-gptcli:8099", token="secret_tok")
    monkeypatch.setattr(ai_video_idea_writer, "get_gptcli_config", lambda db: cfg)
    resting_time = datetime.now(timezone.utc) + timedelta(minutes=30)
    monkeypatch.setattr(ai_video_idea_writer, "gptcli_unavailable_until", lambda: resting_time)

    gen_calls = []
    monkeypatch.setattr(ai_video_idea_writer, "generate_text", lambda *a, **kw: gen_calls.append(True))

    fallback_calls = []

    def fake_fallback(prompt, parse_fn, context=None, fanpage_id=None):
        fallback_calls.append(True)
        return [
            {
                "template_key": "motorsport_tech_explainer",
                "topic": "Resting Fallback Topic",
                "angle": "Angle",
                "score": 80,
                "rationale": "Rationale",
            }
        ], "nine_router"

    monkeypatch.setattr(ai_video_idea_writer.news_copywriter, "_generate_with_fallback", fake_fallback)

    created_count = ai_video_idea_writer.write_ideas(db_session, fanpage, 1)
    assert created_count == 1
    assert len(gen_calls) == 0
    assert len(fallback_calls) == 1

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()

    # (b) config token empty
    cfg_empty = SimpleNamespace(enabled=True, base_url="http://test-gptcli:8099", token="")
    monkeypatch.setattr(ai_video_idea_writer, "get_gptcli_config", lambda db: cfg_empty)
    monkeypatch.setattr(ai_video_idea_writer, "gptcli_unavailable_until", lambda: None)
    fallback_calls.clear()

    created_count = ai_video_idea_writer.write_ideas(db_session, fanpage, 1)
    assert created_count == 1
    assert len(gen_calls) == 0
    assert len(fallback_calls) == 1

    db_session.query(VideoIdea).filter(VideoIdea.fanpage_id == fanpage.id).delete()
    db_session.commit()


def test_top_up_task_routed_off_default_queue():
    from app.tasks.celery_app import celery_app

    assert celery_app.conf.task_routes.get("app.tasks.ai_video_plan.top_up_ai_video_ideas") == {"queue": "gallery"}

