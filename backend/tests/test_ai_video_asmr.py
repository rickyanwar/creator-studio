"""ASMR prompts, speech checks and always-on, segment-timed captions."""

from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.models.ai_video import VideoIdea, VideoJob
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.services import ai_video_fill, ai_video_tts as tts, prompt_polish as polish
from app.services import video_templates as templates
from app.services.ai_video_narration import resolve_narration
from app.tasks import ai_video_clip_qc as qc, ai_video_finalize as finalize
from backend.tests import test_ai_video_clip_qc_task as qc_tests
from backend.tests import test_ai_video_narration_plan as plan_tests
from backend.tests.test_ai_video_clip_qc_task import configured_task
from backend.tests.test_ai_video_rerender import sqlite_engine, db
from backend.tests.test_ai_video_tts_finalize import setup_job
from backend.tests.test_ai_video_tts_prompts import CONFIG, TTS_CASES, variables_for


@pytest.mark.parametrize("key,count", TTS_CASES)
def test_asmr_renders_all_templates(key, count):
    template = templates.get_template(key)
    variables = variables_for(template, count)
    if key == "motorsport_tech_explainer":
        variables["channel_name"] = "SECTOR ONE"
    clips = templates.render_clips(template, variables, count, narration_engine="asmr")
    for clip in clips:
        prompt = clip["prompt"]
        assert template.raw["blocks"]["audio_asmr"] in prompt
        assert "ASMR" in prompt and "no music" in prompt
        assert "Voice-over" not in prompt
        templates._check_tts_prompt(prompt, variables)
        if key == "motorsport_tech_explainer":
            assert '"SECTOR ONE"' in prompt


def asmr_set():
    template = templates.get_template("health_3d_explainer")
    ambience = template.raw["blocks"]["audio_asmr"]
    clips = [{"index": i, "mode": "t2v", "prompt": f'Tissue labelled "SECTOR ONE". {ambience}'}
             for i in (1, 2)]
    return template, clips, ambience


@pytest.mark.parametrize("problem", [None, "music", "quote", "voice"])
def test_asmr_polish(problem):
    _, original, ambience = asmr_set()
    clips = deepcopy(original)
    if problem == "music":
        ambience = ambience.replace("no music", "soft music")
        for clip in clips:
            clip["prompt"] = clip["prompt"].replace("no music", "soft music")
    elif problem == "quote":
        clips[0]["prompt"] += ' "Hello"'
    voice = "Unwanted narrator" if problem == "voice" else ""
    problems = polish.check_polished(original, clips, voice, ambience, narration_engine="asmr")
    assert bool(problems) == (problem is not None)


def test_asmr_instruction_and_polish_pipeline(monkeypatch):
    template, clips, ambience = asmr_set()
    instruction = polish.build_instruction(clips, template=template, niche="health", narration_engine="asmr")
    assert "close-miked ASMR, no music" in instruction
    assert "soft instrumental music bed" not in instruction
    assert "ONE narrator voice sentence" not in instruction
    assert "voice_sentence must be an empty string" in instruction
    answer = {"clips": clips, "voice_sentence": "", "ambience_sentence": ambience, "changes": []}
    monkeypatch.setattr(polish, "_ask", lambda *a, **kw: SimpleNamespace(json=lambda: {"text": json.dumps(answer)}))
    assert polish.polish_clips(clips, template=template, niche="health", config=CONFIG, narration_engine="asmr").ok


@pytest.mark.parametrize("problem", [None, "music", "quote"])
def test_asmr_repolish(monkeypatch, problem):
    template, clips, ambience = asmr_set()
    prompt = clips[0]["prompt"]
    rewritten = prompt.replace("Tissue", "Healthy tissue")
    if problem == "music":
        rewritten = rewritten.replace("no music", "soft music")
    elif problem == "quote":
        rewritten += ' "Hello"'

    def ask(config, instruction, **kwargs):
        assert ambience in instruction
        assert "Keep the narrator voice sentence" not in instruction
        return SimpleNamespace(json=lambda: {"text": json.dumps({"prompt": rewritten, "changes": []})})

    monkeypatch.setattr(polish, "_ask", ask)
    kwargs = dict(mode="t2v", clip_index=1, clips_total=2, issues=[], template=template,
                  niche="health", config=CONFIG, narration_engine="asmr")
    if problem:
        with pytest.raises(ValueError, match="repolish answer invalid"):
            polish.repolish_clip(prompt, **kwargs)
    else:
        assert polish.repolish_clip(prompt, **kwargs) == rewritten


@pytest.mark.parametrize("key,language", [("motorsport_tech_explainer", "English"), ("health_3d_explainer", "French")])
def test_asmr_resolves_only_engine_and_language(key, language):
    page = SimpleNamespace(ai_video_narration={key: {"engine": "asmr", "voice": "Kore"}}, caption_language="fr")
    assert resolve_narration(page, templates.get_template(key), 2, {}) == {"engine": "asmr", "language": language}


class TestPlanner:
    @pytest.fixture
    def db(self):
        yield from plan_tests.db.__wrapped__()

    @pytest.mark.parametrize("research", [False, True])
    def test_asmr_planner(self, db, monkeypatch, research):
        session, ids = db
        page = plan_tests._fanpage(session, ids, name="!!!", ai_video_narration={plan_tests.KEY: {"engine": "asmr"}})
        variables = plan_tests._valid_variables()
        for name in variables:
            if name.startswith("narration_"):
                variables[name] = " ".join(variables[name].split()[:18])
        idea = plan_tests._idea(session, page, clips=2, source="muse" if research else "owner",
                               variables=variables if research else None,
                               title="Research title" if research else None, caption="Caption" if research else None)
        session.commit()
        idea_id = idea.id
        monkeypatch.setattr(plan_tests.planner, "SessionLocal", lambda: session)
        monkeypatch.setattr(plan_tests.planner, "project_can_generate", lambda: True)
        monkeypatch.setattr(plan_tests.planner, "prepare_cover", lambda *a, **kw: None)
        payload = {"variables": variables, "title": "Title", "caption": "Caption"}
        monkeypatch.setattr(ai_video_fill.news_copywriter, "_generate_with_fallback",
                            lambda prompt, parse, **kw: (parse(json.dumps(payload)), "mock"))
        plan_tests.planner.plan_ai_videos.run()
        job = session.query(VideoJob).filter_by(idea_id=idea_id).one()
        assert job.narration == {"engine": "asmr", "language": "English"}
        assert all("ASMR" in clip["prompt"] and "no music" in clip["prompt"] for clip in job.clips)


class TestClipQc:
    @pytest.fixture
    def db(self):
        yield from qc_tests.db.__wrapped__()

    def test_asmr_enqueues_speech(self, db, configured_task, monkeypatch):
        enqueue = Mock()
        monkeypatch.setattr(qc.check_clip_speech, "delay", enqueue)
        job = qc_tests._job(db)
        job.narration = {"engine": "asmr"}
        row = qc_tests._row(db, job)
        result, _ = qc_tests._run(db, row)
        enqueue.assert_called_once_with(row.id)
        qc.logic_check.assert_not_called()
        assert result.verdict is None

    @pytest.mark.parametrize("attempt,verdict", [(1, "retry"), (2, "use_attempt")])
    def test_asmr_speech_retry(self, db, configured_task, attempt, verdict):
        job = qc_tests._job(db)
        job.narration = {"engine": "asmr"}
        job.clip_qc_max_attempts = 4
        if attempt == 2:
            qc_tests._row(db, job, attempt=1, status="failed", verdict="retry", major_count=2,
                          issues=[{"severity": "major", "problem": "bad geometry"}] * 2)
        row = qc_tests._row(db, job, attempt=attempt, speech={"words": 5, "text": "There is a human voice"})
        result, updated = qc_tests._run(db, row)
        assert result.verdict == verdict
        assert "human speech" in result.issues[0]["problem"]
        if attempt == 2:
            assert result.use_attempt == 2
            assert updated.clips[0]["qc"]["accepted_best"] is True


@pytest.mark.parametrize("speech", [[], None, [object()] * 3, RuntimeError("ASR unavailable")])
@pytest.mark.parametrize("captions_enabled", [False, True])
def test_asmr_finalize(db, setup_job, monkeypatch, speech, captions_enabled):
    job, page = setup_job
    page.ai_video_captions_enabled = captions_enabled
    job.narration = {"engine": "asmr", "language": "English"}
    db.commit()
    raw = job.video_path
    transcribe = Mock(side_effect=speech) if isinstance(speech, Exception) else Mock(return_value=speech)
    monkeypatch.setattr("app.services.speech_words.transcribe_words", transcribe)
    normalize = Mock(side_effect=lambda src, out: Path(out).write_bytes(b"normalized"))
    monkeypatch.setattr(tts, "normalize_bed", normalize)
    windows = [(i * 9.9, (i + 1) * 9.9) for i in range(len(job.clips))]
    monkeypatch.setattr(tts, "clip_windows", Mock(return_value=windows))
    narrator = Mock(side_effect=AssertionError("ASMR must not synthesize"))
    monkeypatch.setattr(tts, "narrate_video", narrator)
    captions = Mock(side_effect=AssertionError("ASMR uses segment timings"))
    monkeypatch.setattr("app.services.speech_words.caption_words", captions)
    caption_size = page.ai_video_caption_size

    def render(src, out, overlay_page, words, workdir):
        assert overlay_page.ai_video_captions_enabled is True
        assert overlay_page.ai_video_caption_size == caption_size
        Path(out).write_bytes(b"branded")

    branded = Mock(side_effect=render)
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", branded)
    finalize.finalize_ai_video(job.id)
    db.expire_all()
    normalize.assert_called_once()
    assert normalize.call_args.args[0] == raw
    assert "_asmr_" in normalize.call_args.args[1]
    transcribe.assert_called_once_with(raw, "en")
    branded.assert_called_once()
    assert page.ai_video_captions_enabled == captions_enabled
    assert branded.call_args.args[3]
    segments = job.narration["segments"]
    assert len(segments) == len(job.clips)
    assert segments[0] == {"index": 1, "text": "The rear wing flap opens.", "start": 0.25, "duration": 9.15}
    assert "_asmr_" in job.video_path and "_branded_" in job.video_path
    publish = db.get(PublishJob, job.publish_job_id)
    assert publish.status == (PublishJobStatus.pending_publish if speech == [] else PublishJobStatus.pending_review)
    assert job.qc["overlay"]["timing"] == "asmr"
    narrator.assert_not_called()
    captions.assert_not_called()


def test_asmr_normalize_failure_keeps_raw(db, setup_job, monkeypatch):
    job, _ = setup_job
    job.narration = {"engine": "asmr", "language": "English"}
    db.commit()
    raw = job.video_path
    monkeypatch.setattr(tts, "clip_windows", lambda *a: [(0, 10)] * len(job.clips))
    monkeypatch.setattr(tts, "normalize_bed", Mock(side_effect=RuntimeError("normalize failed")))
    monkeypatch.setattr("app.services.speech_words.transcribe_words", Mock(return_value=[]))
    branded = Mock()
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", branded)
    finalize.finalize_ai_video(job.id)
    db.expire_all()
    assert job.video_path == raw and Path(raw).is_file()
    assert db.get(PublishJob, job.publish_job_id).status == PublishJobStatus.pending_review
    assert "normalize failed" in job.narration["error"]
    branded.assert_not_called()


def test_asmr_clip_qc_skips_safety_net(db, setup_job, monkeypatch):
    job, _ = setup_job
    job.narration = {"engine": "asmr"}
    job.clips = [{**clip, "qc": {"attempt": 1}} for clip in job.clips]
    db.commit()
    monkeypatch.setattr(tts, "clip_windows", lambda *a: [(0, 10)] * len(job.clips))
    monkeypatch.setattr(tts, "normalize_bed", lambda src, out: Path(out).write_bytes(b"normalized"))
    transcribe = Mock()
    monkeypatch.setattr("app.services.speech_words.transcribe_words", transcribe)
    assert finalize._apply_asmr(job, db.get(VideoIdea, job.idea_id), []) == "ok"
    transcribe.assert_not_called()


def test_asmr_rerender_uses_stored_segments(db, setup_job, monkeypatch):
    job, page = setup_job
    job.narration = {"engine": "asmr", "segments": [{"index": 1, "text": "Stored text.", "start": 0.25, "duration": 8.5}]}
    publish = PublishJob(fanpage_id=page.id, content_type="ai_video", status=PublishJobStatus.pending_review,
                         video_path=job.video_path, video_url=job.video_url)
    db.add(publish)
    db.flush()
    job.publish_job_id = publish.id
    job.qc = {"passed": True}
    db.commit()
    estimate = Mock(return_value=[object()])
    monkeypatch.setattr("app.services.ai_video_overlay.estimate_words", estimate)
    branded = Mock(side_effect=lambda src, out, *a: Path(out).write_bytes(b"branded"))
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", branded)
    captions = Mock(side_effect=AssertionError("ASMR must not transcribe captions"))
    monkeypatch.setattr("app.services.speech_words.caption_words", captions)
    finalize.rerender_ai_video_overlay(publish.id)
    db.expire_all()
    estimate.assert_called_once_with(["Stored text."], [(0.25, 8.75)])
    branded.assert_called_once()
    assert branded.call_args.args[2].ai_video_captions_enabled is True
    assert page.ai_video_captions_enabled is False
    assert job.qc["overlay"]["timing"] == "asmr"
    captions.assert_not_called()
