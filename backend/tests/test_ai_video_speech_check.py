"""Speech checks, one speech retry, and fail-closed TTS finalization."""

from unittest.mock import Mock

import pytest

from app.models.ai_video import VideoClipQc
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.services.yt_transcript import Word
from app.tasks import ai_video_clip_qc as qc, ai_video_speech_check as speech
from app.tasks.celery_app import celery_app
from backend.tests.test_ai_video_clip_qc_task import (
    TestSessionLocal, configured_task, db, _job, _row, _run,
)
from backend.tests.test_ai_video_rerender import sqlite_engine, db as finalize_db
from backend.tests.test_ai_video_tts_finalize import setup_job, enable_tts, SEGMENTS


WORDS = [Word(i, i + 0.5, text) for i, text in enumerate("There is a human voice".split())]


def test_tts_defers_logic_until_speech_saved(db, configured_task, monkeypatch):
    enqueue = Mock()
    monkeypatch.setattr(speech.check_clip_speech, "delay", enqueue)
    job = _job(db)
    job.narration = {"engine": "tts"}
    row = _row(db, job)
    result, _ = _run(db, row)
    enqueue.assert_called_once_with(row.id)
    qc.logic_check.assert_not_called()
    assert result.processing_until is None
    assert result.verdict is None


@pytest.mark.parametrize("reply", [WORDS, [], None, RuntimeError("ASR unavailable")])
def test_speech_saves_and_resumes_qc(db, configured_task, monkeypatch, reply):
    job = _job(db)
    row = _row(db, job)
    db.commit()
    transcribe = Mock(side_effect=reply) if isinstance(reply, Exception) else Mock(return_value=reply)
    monkeypatch.setattr(speech, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr("app.services.speech_words.transcribe_words", transcribe)
    enqueue = Mock()
    monkeypatch.setattr(qc.check_clip, "delay", enqueue)
    speech.check_clip_speech(row.id)
    db.expire_all()
    expected = ({"unavailable": True} if reply is None or isinstance(reply, Exception)
                else {"words": len(reply), "text": " ".join(w.text for w in reply)})
    assert db.get(VideoClipQc, row.id).speech == expected
    transcribe.assert_called_once_with(row.clip_path, "en")
    enqueue.assert_called_once_with(row.id)


@pytest.mark.parametrize("attempt,spoken,major,verdict", [
    (1, 5, False, "retry"), (2, 5, False, "use_attempt"),
    (2, 0, True, "retry"), (1, 2, False, "pass"),
])
def test_tts_speech_retry_cap(db, configured_task, monkeypatch, attempt, spoken, major, verdict):
    job = _job(db)
    job.narration = {"engine": "tts"}
    job.clip_qc_max_attempts = 4
    if attempt == 2:
        _row(db, job, attempt=1, status="failed", verdict="retry", major_count=2,
             issues=[{"severity": "major", "problem": "bad geometry"}] * 2)
    row = _row(db, job, attempt=attempt, speech={"words": spoken, "text": "There is a human voice"})
    qc.logic_check.return_value = [{"severity": "major", "problem": "bad geometry"}] if major else []
    result, updated = _run(db, row)
    assert result.verdict == verdict
    if spoken >= 3:
        assert result.major_count == 1
        assert "human speech" in result.issues[0]["problem"]
        assert result.issues[0]["evidence"] == "There is a human voice"
        if attempt == 1:
            from app.services.prompt_polish import repolish_clip
            assert repolish_clip.call_args.kwargs["issues"] == result.issues
        else:
            assert result.use_attempt == 2
            assert updated.clips[0]["qc"]["accepted_best"] is True


@pytest.mark.parametrize("major,verdict", [(False, "pass"), (True, "retry")])
def test_flow_never_checks_speech(db, configured_task, monkeypatch, major, verdict):
    enqueue = Mock()
    monkeypatch.setattr(speech.check_clip_speech, "delay", enqueue)
    qc.logic_check.return_value = [{"severity": "major", "problem": "bad geometry"}] if major else []
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict == verdict
    enqueue.assert_not_called()


def test_speech_task_registered_on_video_queue():
    assert "app.tasks.ai_video_speech_check" in celery_app.conf.include
    assert celery_app.conf.task_routes["app.tasks.ai_video_speech_check.*"] == {"queue": "video"}


class TestFinalizeSafetyNet:
    @pytest.fixture
    def db(self, sqlite_engine):
        yield from finalize_db.__wrapped__(sqlite_engine)

    @pytest.fixture
    def setup_job(self, db, sqlite_engine, tmp_path, monkeypatch):
        return setup_job.__wrapped__(db, sqlite_engine, tmp_path, monkeypatch)

    @pytest.mark.parametrize("reply,reason", [
        (WORDS, "human speech found in the Flow audio"),
        (None, "speech check unavailable"),
        (RuntimeError("ASR unavailable"), "speech check unavailable"),
        ([], None),
    ])
    def test_no_clip_qc(self, db, setup_job, tmp_path, monkeypatch, reply, reason):
        from app.tasks import ai_video_finalize as finalize, publisher

        job, _ = setup_job
        enable_tts(db, job)
        original = job.video_path
        mixed = tmp_path / "mixed.mp4"
        mixed.write_bytes(b"mixed")
        narrator = Mock(return_value={"video_path": str(mixed), "audio_path": "audio.wav",
                                     "model": "tts", "segments": SEGMENTS})
        monkeypatch.setattr("app.services.ai_video_tts.narrate_video", narrator)
        transcribe = Mock(side_effect=reply) if isinstance(reply, Exception) else Mock(return_value=reply)
        monkeypatch.setattr("app.services.speech_words.transcribe_words", transcribe)
        finalize.finalize_ai_video(job.id)
        db.expire_all()
        publish = db.get(PublishJob, job.publish_job_id)
        narrator.assert_called_once()
        transcribe.assert_called_once_with(original, "en")
        assert job.video_path == str(mixed)
        if reason:
            assert publish.status == PublishJobStatus.pending_review
            assert reason in publish.last_error
            assert reason in job.qc["reasons"]
            publisher.publish_job.delay.assert_not_called()
        else:
            assert publish.status == PublishJobStatus.pending_publish

    def test_clip_qc_skips_full_video_transcription(self, db, setup_job, tmp_path, monkeypatch):
        from app.tasks import ai_video_finalize as finalize

        job, _ = setup_job
        enable_tts(db, job)
        job.clips = [{**clip, "qc": {"attempt": 1}} for clip in job.clips]
        db.commit()
        mixed = tmp_path / "mixed.mp4"
        mixed.write_bytes(b"mixed")
        monkeypatch.setattr("app.services.ai_video_tts.narrate_video", Mock(return_value={
            "video_path": str(mixed), "audio_path": "audio.wav", "model": "tts", "segments": SEGMENTS,
        }))
        transcribe = Mock()
        monkeypatch.setattr("app.services.speech_words.transcribe_words", transcribe)
        finalize.finalize_ai_video(job.id)
        db.expire_all()
        transcribe.assert_not_called()
        assert db.get(PublishJob, job.publish_job_id).status == PublishJobStatus.pending_publish

    def test_speech_review_still_brands_the_narrated_video(self, db, setup_job, tmp_path, monkeypatch):
        """A speech-check review keeps the narration and the overlays; only the publish status changes."""
        from app.tasks import ai_video_finalize as finalize

        job, page = setup_job
        enable_tts(db, job)
        page.ai_video_captions_enabled = True
        db.commit()
        mixed = tmp_path / "mixed.mp4"
        mixed.write_bytes(b"mixed")
        monkeypatch.setattr("app.services.ai_video_tts.narrate_video", Mock(return_value={
            "video_path": str(mixed), "audio_path": "audio.wav", "model": "tts", "segments": SEGMENTS,
        }))
        monkeypatch.setattr("app.services.speech_words.transcribe_words", Mock(return_value=None))
        branded = Mock()
        monkeypatch.setattr("app.services.ai_video_overlay.render_branded", branded)
        monkeypatch.setattr("app.tasks.ai_video_finalize._tts_caption_words", Mock(return_value=[]))
        finalize.finalize_ai_video(job.id)
        db.expire_all()
        branded.assert_called_once()
        assert db.get(PublishJob, job.publish_job_id).status == PublishJobStatus.pending_review
