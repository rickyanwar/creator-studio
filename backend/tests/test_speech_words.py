import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import VideoIdea, VideoJob
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.settings import Settings
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.speech_words import (
    align_to_script,
    caption_words,
    transcribe_words,
)
from app.services.yt_transcript import Word
from app.tasks import ai_video_finalize, publisher
from tests.test_ai_video_qc_finalize import make_video


@pytest.fixture(scope="module", autouse=True)
def create_tables():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def test_align_to_script_spelling_extra_and_dropped():
    spoken = [
        Word(0.0, 0.5, "le"),
        Word(0.5, 1.2, "volet"),
        Word(1.2, 1.8, "supérieur"),
        Word(1.8, 2.3, "tourne"),
        Word(2.3, 2.7, "vite"),
    ]
    script_texts = ["Le volet supérieur tourne."]
    aligned = align_to_script(spoken, script_texts)

    # Script spelling used for matching words with spoken timing
    assert aligned[0] == Word(0.0, 0.5, "Le")
    assert aligned[1] == Word(0.5, 1.2, "volet")
    assert aligned[2] == Word(1.2, 1.8, "supérieur")
    assert aligned[3] == Word(1.8, 2.3, "tourne.")
    # Extra spoken word with no script counterpart (delete) kept as spoken
    assert aligned[4] == Word(2.3, 2.7, "vite")
    assert len(aligned) == 5


def test_align_to_script_unspoken_script_dropped():
    spoken = [Word(0.0, 0.5, "le"), Word(0.5, 1.0, "volet")]
    script_texts = ["Le volet fermé."]
    aligned = align_to_script(spoken, script_texts)
    assert aligned == [Word(0.0, 0.5, "Le"), Word(0.5, 1.0, "volet")]


def test_align_to_script_replace_spreads_script_words():
    # Single word replacement (e.g. ASR misrecognition: "éleron" -> script "aileron")
    spoken = [Word(7.65, 7.93, "éleron"), Word(7.93, 8.39, "arrière")]
    script_texts = ["aileron arrière"]
    aligned = align_to_script(spoken, script_texts)
    assert aligned[0] == Word(7.65, 7.93, "aileron")
    assert aligned[1] == Word(7.93, 8.39, "arrière")

    # Multi-word replace: 1 spoken token replaced by 2 script tokens proportional to length
    spoken_multi = [Word(1.0, 3.0, "ab")]  # span = 2.0s
    script_multi = ["un", "cinq"]  # len 2 and len 4 -> 1/3 and 2/3 of 2.0s
    aligned_multi = align_to_script(spoken_multi, script_multi)
    assert len(aligned_multi) == 2
    assert aligned_multi[0].text == "un"
    assert round(aligned_multi[0].start, 2) == 1.0
    assert round(aligned_multi[0].end, 2) == 1.67
    assert aligned_multi[1].text == "cinq"
    assert round(aligned_multi[1].start, 2) == 1.67
    assert round(aligned_multi[1].end, 2) == 3.0


def test_align_to_script_accents_and_punctuation():
    spoken = [Word(1.0, 2.0, "superieur")]
    script_texts = ["Supérieur,"]
    aligned = align_to_script(spoken, script_texts)
    assert len(aligned) == 1
    assert aligned[0] == Word(1.0, 2.0, "Supérieur,")


def test_caption_words_returns_none_when_transcription_none():
    with patch("app.services.speech_words.transcribe_words", return_value=None):
        assert caption_words("fake.mp4", "fr", ["Le volet tourne."]) is None


def test_caption_words_returns_none_when_fewer_than_half_script_words():
    # 6 script words, 2 spoken words (< 0.5 * 6)
    spoken = [Word(0.0, 0.5, "un"), Word(0.5, 1.0, "deux")]
    script_texts = ["un deux trois quatre cinq six"]
    with patch("app.services.speech_words.transcribe_words", return_value=spoken):
        assert caption_words("fake.mp4", "fr", script_texts) is None


def test_caption_words_success_when_sufficient_words():
    spoken = [
        Word(0.0, 0.5, "le"),
        Word(0.5, 1.0, "volet"),
        Word(1.0, 1.5, "tourne"),
    ]
    script_texts = ["Le volet tourne."]
    with patch("app.services.speech_words.transcribe_words", return_value=spoken):
        result = caption_words("fake.mp4", "fr", script_texts)
        assert result is not None
        assert [w.text for w in result] == ["Le", "volet", "tourne."]


def test_transcribe_words_returns_none_on_model_exception():
    with patch("app.services.speech_words._get_model", side_effect=RuntimeError("model error")):
        assert transcribe_words("fake.mp4", "fr") is None


def test_transcribe_words_success():
    mock_word1 = MagicMock(start=0.1, end=0.6, word=" bonjour ")
    mock_word2 = MagicMock(start=0.7, end=1.2, word="")
    mock_segment = MagicMock(words=[mock_word1, mock_word2])
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_segment], MagicMock())

    with patch("app.services.speech_words._get_model", return_value=mock_model):
        words = transcribe_words("fake.mp4", "fr")
        assert words == [Word(0.1, 0.6, "bonjour")]


def test_transcribe_words_cleans_up_model_with_gc():
    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([], MagicMock())

    with patch("app.services.speech_words._get_model", return_value=mock_model), \
         patch("gc.collect") as mock_gc:
        transcribe_words("fake.mp4", "fr")
        assert mock_gc.called


def _seed_finalize_job(db, tmp_path):
    fanpage = TargetFanpage(
        repliz_account_id=f"test-asr-{uuid.uuid4().hex}",
        name="TestASR",
        ai_video_publish_mode=PublishMode.auto,
        ai_video_captions_enabled=True,
        ai_video_logo_enabled=False,
        caption_language="fr",
    )
    db.add(fanpage)
    db.flush()

    variables = {"narration_1": "Le volet supérieur tourne."}
    idea = VideoIdea(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic="topic",
        variables=variables,
        status="queued",
    )
    db.add(idea)
    db.flush()

    video = tmp_path / f"job-{uuid.uuid4().hex}.mp4"
    make_video(video, duration=4)
    job = VideoJob(
        idea_id=idea.id,
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=4,
        clips=[{"index": 1, "mode": "t2v", "prompt": "x"}],
        credits_per_clip=15,
        credits_estimate=15,
        status="uploaded",
        video_path=str(video),
        video_url="http://example/video.mp4",
        thumbnail_path=str(tmp_path / "thumb.jpg"),
        thumbnail_url="http://example/thumb.jpg",
        duration_s=4,
    )
    db.add(job)
    settings = db.query(Settings).filter_by(id=1).first() or Settings(id=1)
    settings.ai_video_vision_qc_enabled = False
    db.add(settings)
    db.commit()
    return job.id, fanpage.id


def test_finalize_overlay_timing_voice(db, tmp_path):
    job_id, fp_id = _seed_finalize_job(db, tmp_path)
    with patch("app.services.speech_words.caption_words", return_value=[Word(0.5, 1.0, "Le")]), \
         patch("app.services.ai_video_overlay.render_branded") as render, \
         patch("app.services.ai_video_qc.contact_sheet", return_value=b"jpeg"), \
         patch.object(publisher.publish_job, "delay"):

        def fake_render(src, out, *a, **kw):
            Path(out).write_bytes(Path(src).read_bytes())
        render.side_effect = fake_render

        ai_video_finalize.finalize_ai_video(job_id)

    job = db.query(VideoJob).filter_by(id=job_id).first()
    assert job.qc.get("overlay", {}).get("timing") == "voice"
    assert job.qc.get("overlay", {}).get("words") == 1


def test_finalize_overlay_timing_estimate_fallback(db, tmp_path):
    job_id, fp_id = _seed_finalize_job(db, tmp_path)
    with patch("app.services.speech_words.caption_words", return_value=None), \
         patch("app.services.ai_video_overlay.speech_windows", return_value=[(0.4, 3.6)]), \
         patch("app.services.ai_video_overlay.estimate_words", return_value=[Word(0.5, 1.0, "EST")]), \
         patch("app.services.ai_video_overlay.render_branded") as render, \
         patch("app.services.ai_video_qc.contact_sheet", return_value=b"jpeg"), \
         patch.object(publisher.publish_job, "delay"):

        def fake_render(src, out, *a, **kw):
            Path(out).write_bytes(Path(src).read_bytes())
        render.side_effect = fake_render

        ai_video_finalize.finalize_ai_video(job_id)

    job = db.query(VideoJob).filter_by(id=job_id).first()
    assert job.qc.get("overlay", {}).get("timing") == "estimate"
    assert job.qc.get("overlay", {}).get("words") == 1
