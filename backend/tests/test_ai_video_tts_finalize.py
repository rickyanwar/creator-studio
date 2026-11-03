"""Narrator finalization, caption timing, and retention regression checks."""

from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import sessionmaker

from app.models.ai_video import VideoJob
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_tts as tts
from app.tasks import ai_video_finalize as finalize, cleanup, publisher
from backend.tests.test_ai_video_rerender import sqlite_engine, db
from backend.tests.test_ai_video_qc_finalize import seed_job


SEGMENTS = [{"text": "The flap opens.", "start": 0.25, "duration": 2.0}]


@pytest.fixture
def setup_job(db, sqlite_engine, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tests.test_ai_video_qc_finalize.make_video", lambda path, **kw: path.write_bytes(b"video"))
    job_id, page_id, _ = seed_job(db, tmp_path, variables={"narration_1": "The rear wing flap opens."})
    job = db.get(VideoJob, job_id)
    page = db.get(TargetFanpage, page_id)
    page.ai_video_cover_enabled = False
    page.ai_video_logo_enabled = False
    page.ai_video_captions_enabled = False
    job.thumbnail_path = None
    db.commit()
    monkeypatch.setattr(finalize, "SessionLocal", sessionmaker(bind=sqlite_engine))
    monkeypatch.setattr("app.services.ai_video_qc.technical_check", lambda *a: {"passed": True, "reasons": []})
    monkeypatch.setattr(publisher.publish_job, "delay", Mock())
    monkeypatch.setattr("subprocess.run", Mock())
    return job, page


def enable_tts(db, job):
    job.narration = {"engine": "tts", "voice": "Kore", "style": "Calm", "language": "English"}
    db.commit()


@pytest.mark.parametrize("narration", [None, {"engine": "flow"}])
def test_flow_finalize_unchanged(db, setup_job, monkeypatch, narration):
    job, _ = setup_job
    job.narration = narration
    db.commit()
    raw = job.video_path
    narrator = Mock(side_effect=AssertionError("Flow must not synthesize"))
    monkeypatch.setattr(tts, "narrate_video", narrator)
    finalize.finalize_ai_video(job.id)
    db.expire_all()
    assert job.video_path == raw
    assert db.get(PublishJob, job.publish_job_id).status == PublishJobStatus.pending_publish
    narrator.assert_not_called()


def test_tts_finalize_success_and_captions(db, setup_job, tmp_path, monkeypatch):
    job, page = setup_job
    enable_tts(db, job)
    page.ai_video_captions_enabled = True
    db.commit()
    raw = Path(job.video_path)
    mixed, audio = tmp_path / "mixed.mp4", tmp_path / "narrator.wav"
    mixed.write_bytes(b"mixed")
    audio.write_bytes(b"audio")
    narrator = Mock(return_value={"video_path": str(mixed), "audio_path": str(audio), "model": "tts-model", "segments": SEGMENTS})
    monkeypatch.setattr(tts, "narrate_video", narrator)
    captions = Mock(return_value=[object()])
    monkeypatch.setattr("app.services.speech_words.caption_words", captions)
    # No clip QC ran, so finalize checks the Flow audio for speech; none here.
    monkeypatch.setattr("app.services.speech_words.transcribe_words", Mock(return_value=[]))
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", lambda raw, out, *a: Path(out).write_bytes(b"branded"))
    finalize.finalize_ai_video(job.id)
    db.expire_all()
    assert "mixed_branded_" in job.video_path
    assert not raw.exists()
    assert job.narration["segments"] == SEGMENTS
    assert job.narration["audio_path"] == str(audio)
    assert job.narration["model"] == "tts-model"
    assert captions.call_args.args[0] == str(audio)
    assert captions.call_args.args[2] == ["The flap opens."]
    assert job.qc["overlay"]["timing"] == "tts"
    assert db.get(PublishJob, job.publish_job_id).status == PublishJobStatus.pending_publish
    publisher.publish_job.delay.assert_called_once()
    assert not list(tmp_path.glob("tts_*"))


def test_tts_finalize_without_overlays_keeps_mixed_path(db, setup_job, tmp_path, monkeypatch):
    job, _ = setup_job
    enable_tts(db, job)
    mixed = tmp_path / "mixed.mp4"
    mixed.write_bytes(b"mixed")
    monkeypatch.setattr(tts, "narrate_video", Mock(return_value={"video_path": str(mixed), "audio_path": "audio.wav", "model": "tts", "segments": SEGMENTS}))
    finalize.finalize_ai_video(job.id)
    db.expire_all()
    assert job.video_path == str(mixed)
    assert job.video_url == "http://example/mixed.mp4"


@pytest.mark.parametrize("missing_text", [False, True])
def test_tts_failure_keeps_raw_and_requires_review(db, setup_job, monkeypatch, missing_text):
    job, _ = setup_job
    enable_tts(db, job)
    if missing_text:
        from app.models.ai_video import VideoIdea
        db.get(VideoIdea, job.idea_id).variables = {}
        db.commit()
    raw = Path(job.video_path)
    narrator = Mock(side_effect=tts.TtsError("synthesis failed"))
    monkeypatch.setattr(tts, "narrate_video", narrator)
    finalize.finalize_ai_video(job.id)
    db.expire_all()
    publish = db.get(PublishJob, job.publish_job_id)
    assert publish.status == PublishJobStatus.pending_review
    assert "tts narration failed" in publish.last_error
    assert raw.exists()
    assert job.narration["error"]
    assert not job.qc["passed"]
    publisher.publish_job.delay.assert_not_called()
    if missing_text:
        narrator.assert_not_called()


@pytest.mark.parametrize("reply", ["", "one two three four", 'A "quote".', "A “quote”."])
def test_shorten_rejects_invalid_reply(monkeypatch, reply):
    def generate(prompt, parse_fn, *, context):
        assert context == "ai_video_tts_shorten"
        assert "Do not add any fact that is not in the sentence" in prompt
        return parse_fn(reply), "router"
    monkeypatch.setattr("app.services.news_copywriter._generate_with_fallback", generate)
    with pytest.raises(ValueError):
        tts.shorten_narration("Original sentence.", 3, "English")


def test_shorten_returns_valid_sentence(monkeypatch):
    monkeypatch.setattr("app.services.news_copywriter._generate_with_fallback", lambda prompt, parse_fn, **kw: (parse_fn("  Flap opens.  "), "router"))
    assert tts.shorten_narration("The rear flap opens.", 3, "English") == "Flap opens."


def test_narrate_keeps_audio_and_wires_shortener(tmp_path, monkeypatch):
    raw = tmp_path / "raw.mp4"
    raw.write_bytes(b"raw")
    output = tmp_path / "work"
    output.mkdir()
    wav = output / "narration.wav"
    wav.write_bytes(b"wav")
    job = SimpleNamespace(id=7, video_path=str(raw), clip_seconds=None, narration={"voice": "Kore", "style": "Calm", "language": "English"})
    shortener = Mock(return_value="Short.")
    monkeypatch.setattr(tts, "shorten_narration", shortener)
    def build(*args, **kwargs):
        assert args == (str(raw), ["Long sentence."], "Kore", "Calm", "English", str(output))
        assert kwargs["clip_seconds"] == 10.0
        assert kwargs["shorten"]("Long sentence.", 2) == "Short."
        return {"audio_path": str(wav), "model": "tts", "segments": SEGMENTS}
    monkeypatch.setattr(tts, "build_narration", build)
    mix = Mock()
    monkeypatch.setattr(tts, "mix_narration", mix)
    result = tts.narrate_video(job, ["Long sentence."], str(output))
    assert Path(result["audio_path"]).read_bytes() == b"wav"
    assert Path(result["audio_path"]).parent == tmp_path
    assert not wav.exists()
    assert "raw_tts_" in result["video_path"]
    mix.assert_called_once_with(str(raw), str(wav), result["video_path"], segments=SEGMENTS)


@pytest.mark.parametrize("audio_exists", [True, False])
def test_tts_rerender_uses_stored_segments(db, setup_job, tmp_path, monkeypatch, audio_exists):
    job, page = setup_job
    enable_tts(db, job)
    page.ai_video_captions_enabled = True
    audio = tmp_path / "narrator.wav"
    if audio_exists:
        audio.write_bytes(b"wav")
    job.narration = {**job.narration, "segments": SEGMENTS, "audio_path": str(audio)}
    publish = PublishJob(fanpage_id=page.id, content_type="ai_video", status=PublishJobStatus.pending_review, video_path=job.video_path, video_url=job.video_url)
    db.add(publish)
    db.flush()
    job.publish_job_id = publish.id
    job.qc = {"passed": True}
    db.commit()
    captions = Mock(return_value=None)
    estimate = Mock(return_value=[])
    monkeypatch.setattr("app.services.speech_words.caption_words", captions)
    monkeypatch.setattr("app.services.ai_video_overlay.estimate_words", estimate)
    monkeypatch.setattr("app.services.ai_video_overlay.render_branded", lambda raw, out, *a: Path(out).write_bytes(b"branded"))
    finalize.rerender_ai_video_overlay(publish.id)
    db.expire_all()
    if audio_exists:
        assert captions.call_args.args[0] == str(audio)
        assert captions.call_args.args[2] == ["The flap opens."]
    else:
        captions.assert_not_called()
    estimate.assert_called_once_with(["The flap opens."], [(0.25, 2.25)])
    assert job.qc["overlay"]["timing"] == "tts"


def test_cleanup_removes_narration_only_with_expired_video(db, setup_job, tmp_path, sqlite_engine, monkeypatch):
    job, page = setup_job
    media = tmp_path / "ai_videos"
    media.mkdir()
    raw, audio = media / "raw.mp4", media / "narration.wav"
    raw.write_bytes(b"video")
    audio.write_bytes(b"wav")
    job.video_path = str(raw)
    job.narration = {"engine": "tts", "audio_path": str(audio)}
    old = datetime.utcnow() - timedelta(days=60)
    publish = PublishJob(fanpage_id=page.id, content_type="ai_video", status=PublishJobStatus.published, video_path=str(raw), published_at=old, updated_at=old)
    db.add(publish)
    db.flush()
    job.publish_job_id, job.status = publish.id, "done"
    db.commit()
    monkeypatch.setattr(cleanup, "SessionLocal", sessionmaker(bind=sqlite_engine))
    monkeypatch.setattr(cleanup, "settings", SimpleNamespace(storage_base_path=str(tmp_path)))
    monkeypatch.setattr(cleanup, "_job_go_live_pending", lambda *a: set())
    cleanup.cleanup_old_ai_videos()
    assert not raw.exists()
    assert not audio.exists()
