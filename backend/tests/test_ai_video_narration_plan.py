"""Narrator selection and engine propagation through planning, prepare and QC."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.models.ai_video import VideoJob
from app.services import ai_video_fill, ai_video_prepare, prompt_polish
from app.services.ai_video_narration import engine_for, resolve_narration, tts_language, tts_style
from app.services.ai_video_prepare import idea_fingerprint, valid_prepared, _resume_draft
from app.services.prompt_polish import PolishInterrupted, PolishResult
from app.services.video_templates import get_narration_budget, get_template
import app.tasks.ai_video_plan as planner
from tests import test_ai_video_clip_qc_task as qc_tests
from tests.test_ai_video_clip_qc_task import configured_task
from tests.test_ai_video_ideas_plan import _valid_variables
from tests.test_ai_video_prepare import CFG, _fanpage, _idea, db


KEY = "motorsport_tech_explainer"


def page(key=KEY, voice=None, **kwargs):
    return SimpleNamespace(ai_video_narration={key: {"engine": "tts", "voice": voice}}, **kwargs)


@pytest.mark.parametrize("engine", ["tts", "asmr"])
def test_engine_selection(engine):
    template = get_template(KEY)
    fanpage = SimpleNamespace(ai_video_narration={KEY: {"engine": engine}})
    assert engine_for(SimpleNamespace(), template, 2) == "flow"
    assert engine_for(fanpage, template, 2) == engine
    assert engine_for(fanpage, template, 1) == "flow"
    fanpage.ai_video_narration = {"motorsport_tech_dive": {"engine": engine}}
    assert engine_for(fanpage, get_template("motorsport_tech_dive"), 2) == "flow"


@pytest.mark.parametrize("preset", ["EN-PADDOCK", "EN-DOC", None])
def test_style_uses_template_narrator(preset):
    template = get_template(KEY)
    style = tts_style(template, preset)
    assert style.startswith("Read this as an engaging")
    assert "Voice-over" not in style and "says" not in style
    assert style == "Read this as an engaging documentary narrator, confident, clear and warm, with natural energy and a lively, flowing pace"


def test_health_style_overrides_shared():
    template = get_template("health_3d_explainer")
    assert tts_style(template, "EN-PADDOCK") == (
        "Read this as a clear, warm and friendly health educator with natural energy and a calm, flowing pace"
    )


@pytest.mark.parametrize("engine,expected", [("flow", "22-24"), ("tts", "22-24"), ("asmr", "18-20")])
def test_narration_budget_by_engine(engine, expected):
    assert get_narration_budget({"narration_words": {"en": "22-24"}}, "en", engine) == expected


@pytest.mark.parametrize("lang,flow,shorter", [("fr", "18-20", "14-16"), ("de", "17-19", "14-16")])
def test_narration_budget_scales_language_budget(lang, flow, shorter):
    clip = {"narration_words": {"en": "22-24", "fr": "18-20"}}
    assert get_narration_budget(clip, lang) == flow
    assert get_narration_budget(clip, lang, "tts") == flow
    assert get_narration_budget(clip, lang, "asmr") == shorter


@pytest.mark.parametrize("engine,expected", [("flow", "22-24"), ("tts", "22-24"), ("asmr", "18-20")])
def test_fill_prompt_uses_engine_budget(engine, expected):
    fanpage = page(caption_language="en")
    fanpage.ai_video_narration[KEY]["engine"] = engine
    idea = SimpleNamespace(topic="Rear wing airflow", angle=None, research_notes=None, variables=None)
    prompt = ai_video_fill.build_fill_prompt(get_template(KEY), idea, fanpage, 2)
    assert f'"narration_1": "{expected}"' in prompt


def test_resolve_voice_and_language():
    template = get_template(KEY)
    variables = {"narrator_preset": "EN-PADDOCK"}
    assert resolve_narration(page(voice="Kore"), template, 2, variables)["voice"] == "Kore"
    assert resolve_narration(page(), template, 2, variables)["voice"] == "Iapetus"
    assert resolve_narration(SimpleNamespace(), template, 2, variables) is None
    assert tts_language(template, page(caption_language="fr")) == "English"
    assert tts_language(get_template("health_3d_explainer"), page(caption_language="fr-CA")) == "French"


@pytest.mark.parametrize("research", [False, True])
def test_planner_direct_fill_persists_tts(db, monkeypatch, research):
    session, ids = db
    fp = _fanpage(session, ids, name="!!!", ai_video_narration=page().ai_video_narration)
    variables = _valid_variables()
    for name in variables:
        if name.startswith("narration_"):
            variables[name] = " ".join(["word"] * 20)
    idea = _idea(session, fp, clips=2, source="muse" if research else "owner",
                 variables=variables if research else None,
                 title="Research title" if research else None, caption="Caption" if research else None)
    session.commit()
    idea_id = idea.id
    monkeypatch.setattr(planner, "SessionLocal", lambda: session)
    monkeypatch.setattr(planner, "project_can_generate", lambda: True)
    monkeypatch.setattr(planner, "prepare_cover", lambda *args, **kwargs: None)
    payload = {"variables": variables, "title": "Title", "caption": "Caption"}

    def generate(prompt, parse, **kwargs):
        return parse(json.dumps(payload)), "mock"

    monkeypatch.setattr(ai_video_fill.news_copywriter, "_generate_with_fallback", generate)
    planner.plan_ai_videos.run()
    job = session.query(VideoJob).filter_by(idea_id=idea_id).one()
    assert set(job.narration) == {"engine", "voice", "style", "language"}
    assert job.narration["engine"] == "tts"
    assert job.narration["voice"]
    assert job.narration["style"].startswith("Read this as a")
    assert job.narration["language"] == "English"
    assert len(job.clips) == 2
    assert all('"' not in clip["prompt"] for clip in job.clips)


def test_stale_flow_prepared_cannot_create_tts_job(db, monkeypatch):
    session, ids = db
    fp = _fanpage(session, ids, ai_video_narration=page().ai_video_narration,
                  ai_video_prompt_polish_enabled=True)
    idea = _idea(session, fp, clips=2)
    idea.prepared = {"version": 1, "fingerprint": idea_fingerprint(idea), "template_key": KEY,
                     "clips_count": 2, "polished": True, "clips": [{"index": 1, "prompt": "stale"}],
                     "narration_engine": "flow"}
    session.commit()
    assert valid_prepared(idea) is not None
    assert valid_prepared(idea, engine="tts") is None
    monkeypatch.setattr(planner, "SessionLocal", lambda: session)
    monkeypatch.setattr(planner, "project_can_generate", lambda: True)
    planner.plan_ai_videos.run()
    assert session.query(VideoJob).filter_by(idea_id=idea.id).count() == 0


def test_planner_polished_path_persists_tts(db, monkeypatch):
    session, ids = db
    fp = _fanpage(session, ids, ai_video_narration=page().ai_video_narration,
                  ai_video_prompt_polish_enabled=True)
    idea = _idea(session, fp, clips=2)
    idea.prepared = {"version": 1, "fingerprint": idea_fingerprint(idea), "template_key": KEY,
                     "clips_count": 2, "polished": True,
                     "clips": [{"index": i, "mode": "t2v", "prompt": "ambient scene"} for i in (1, 2)],
                     "narration_engine": "tts", "variables": {"narrator_preset": "EN-PADDOCK"},
                     "title": "Title", "caption": "Caption"}
    session.commit()
    idea_id = idea.id
    monkeypatch.setattr(planner, "SessionLocal", lambda: session)
    monkeypatch.setattr(planner, "project_can_generate", lambda: True)
    monkeypatch.setattr(planner, "prepare_cover", lambda *args, **kwargs: None)
    planner.plan_ai_videos.run()
    job = session.query(VideoJob).filter_by(idea_id=idea_id).one()
    assert job.narration == resolve_narration(page(), get_template(KEY), 2, {"narrator_preset": "EN-PADDOCK"})


@pytest.mark.parametrize("outcome", ["polished", "draft", "render_fallback"])
def test_prepare_propagates_engine_and_stores_it(db, outcome):
    session, ids = db
    key = "motorcycle_commercial" if outcome == "render_fallback" else KEY
    fp = _fanpage(session, ids, ai_video_narration=page(key).ai_video_narration)
    idea = _idea(session, fp, clips=2, template_key=key)
    rendered = [{"index": 1, "mode": "t2v", "prompt": "ambient scene"}]
    result = PolishResult(ok=outcome == "polished", clips=rendered, problems=["issue"], attempts=1)
    interrupted = PolishInterrupted("upstream", answer="answer", problems=["issue"], attempts_used=1,
                                    account="mock", seconds=1)
    with patch.object(ai_video_prepare, "fill_idea", return_value=({}, "Title", "Caption", rendered, "mock")), \
         patch.object(ai_video_prepare, "polish_clips", return_value=result,
                      side_effect=interrupted if outcome == "draft" else None) as polish:
        if outcome == "draft":
            with pytest.raises(PolishInterrupted):
                ai_video_prepare.prepare_idea(session, idea, fp, CFG)
        else:
            assert ai_video_prepare.prepare_idea(session, idea, fp, CFG) == "prepared"
        assert polish.call_args.kwargs["narration_engine"] == "tts"
    assert idea.prepared["narration_engine"] == "tts"
    if outcome == "draft":
        assert _resume_draft(idea, 2, engine="flow") is None
        assert _resume_draft(idea, 2, engine="tts") is not None
        with patch.object(ai_video_prepare, "polish_clips", return_value=PolishResult(ok=True, clips=rendered)) as polish:
            assert ai_video_prepare.prepare_idea(session, idea, fp, CFG) == "prepared"
        assert polish.call_args.kwargs["narration_engine"] == "tts"
        assert polish.call_args.kwargs["resume"]["answer"] == "answer"


@pytest.fixture
def qc_session():
    yield from qc_tests.db.__wrapped__()


def test_qc_repolish_uses_job_engine(qc_session, configured_task, monkeypatch):
    job = qc_tests._job(qc_session)
    job.narration = {"engine": "tts"}
    row = qc_tests._row(qc_session, job)
    row.speech = {"words": 0, "text": ""}  # speech already checked on the video queue
    qc_session.commit()
    qc_tests.task.logic_check.return_value = [{"problem": "floating part", "severity": "major"}]
    repolish = MagicMock(return_value="repaired scene")
    monkeypatch.setattr(prompt_polish, "repolish_clip", repolish)
    result, _ = qc_tests._run(qc_session, row)
    assert result.verdict == "retry"
    assert repolish.call_args.kwargs["narration_engine"] == "tts"
