from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.qa_toggles import QA_MODE_COLUMNS, qa_enabled, qa_mode_for_content_kind
from app.services.pro_poster.pipeline import compose_pro_poster
from app.services.pro_poster.types import (
    ArtDirection,
    EventContext,
    PhotoCandidate,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    QAResult,
    Subject,
)
from app.services.visual_engine import GenerationResult


def test_qa_enabled_truth_table():
    # Arrange / Act / Assert

    # None fanpage -> True (fail closed)
    assert qa_enabled(None, "news") is True
    assert qa_enabled(None, "ig") is True
    assert qa_enabled(None, "facebook") is True
    assert qa_enabled(None, "ai_video") is True

    # Attribute missing -> True
    fp_empty = SimpleNamespace()
    assert qa_enabled(fp_empty, "news") is True
    assert qa_enabled(fp_empty, "ig") is True
    assert qa_enabled(fp_empty, "facebook") is True
    assert qa_enabled(fp_empty, "ai_video") is True

    # Attribute is None -> True
    fp_none = SimpleNamespace(
        qa_news_enabled=None,
        qa_ig_enabled=None,
        qa_facebook_enabled=None,
        qa_ai_video_enabled=None,
    )
    assert qa_enabled(fp_none, "news") is True
    assert qa_enabled(fp_none, "ig") is True
    assert qa_enabled(fp_none, "facebook") is True
    assert qa_enabled(fp_none, "ai_video") is True

    # Explicit False -> False
    fp_off = SimpleNamespace(
        qa_news_enabled=False,
        qa_ig_enabled=False,
        qa_facebook_enabled=False,
        qa_ai_video_enabled=False,
    )
    assert qa_enabled(fp_off, "news") is False
    assert qa_enabled(fp_off, "ig") is False
    assert qa_enabled(fp_off, "facebook") is False
    assert qa_enabled(fp_off, "ai_video") is False

    # Explicit True -> True
    fp_on = SimpleNamespace(
        qa_news_enabled=True,
        qa_ig_enabled=True,
        qa_facebook_enabled=True,
        qa_ai_video_enabled=True,
    )
    assert qa_enabled(fp_on, "news") is True
    assert qa_enabled(fp_on, "ig") is True
    assert qa_enabled(fp_on, "facebook") is True
    assert qa_enabled(fp_on, "ai_video") is True

    # Unknown mode -> True (fail closed)
    assert qa_enabled(fp_off, "unknown_mode") is True
    assert qa_enabled(fp_on, "unknown_mode") is True
    assert qa_enabled(None, "unknown_mode") is True


def test_qa_mode_for_content_kind():
    # Arrange / Act / Assert
    assert qa_mode_for_content_kind("news") == "news"
    assert qa_mode_for_content_kind("facebook_photo") == "facebook"
    assert qa_mode_for_content_kind("facebook") == "facebook"
    assert qa_mode_for_content_kind("ig") == "ig"
    assert qa_mode_for_content_kind("ig_recreate") == "ig"
    assert qa_mode_for_content_kind(None) == "news"
    assert qa_mode_for_content_kind("") == "news"
    assert qa_mode_for_content_kind("other_kind") == "news"


def _make_brief(job_id: int = 1) -> PosterBrief:
    return PosterBrief(
        job_id=job_id,
        subjects=(Subject(name="Max Verstappen", role="hero"),),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car", circuit="Monza"),
        story_type="result",
        mood="happy",
        badge_label="F1 NEWS",
        accent_colors=("#FF8000",),
    )


def _make_selection() -> PhotoSelection:
    chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=42, path_or_url="/tmp/v.jpg", role="hero"),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
    )
    return PhotoSelection(hero=chk, context=(), rejected=())


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_pro_poster_qa_disabled_skips_check_safety(
    mock_finish, mock_gen, mock_safety, mock_direct, mock_select
):
    # Arrange
    mock_safety.side_effect = AssertionError("qa.check_safety should not be called when qa_news_enabled is False")
    db = MagicMock()
    fanpage = SimpleNamespace(id=10, qa_news_enabled=False)
    job = MagicMock(id=102, design_analysis_json={})
    article = MagicMock(id=202)

    mock_select.return_value = _make_selection()
    mock_direct.return_value = ArtDirection(layout="hero_with_context", prompt="Poster prompt", refs=("/tmp/v.jpg",))
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)

    # Act
    with patch("app.services.pro_poster.pipeline.build_brief", return_value=_make_brief(102)), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    # Assert
    assert result.image == b"finished_png"
    assert result.qa is None
    assert result.fallback_reason is None
    assert result.attempts == 1
    mock_safety.assert_not_called()


@patch("app.database.SessionLocal")
@patch("app.services.ai_video_qc.technical_check")
def test_ai_video_finalize_skips_vision_qc_when_qa_ai_video_disabled(
    mock_tech, mock_session_local
):
    # Arrange
    from app.tasks.ai_video_finalize import finalize_ai_video

    mock_db = MagicMock()
    mock_session_local.return_value = mock_db

    mock_job = MagicMock(
        id=55,
        status="uploaded",
        idea_id=1,
        fanpage_id=2,
        video_path="/tmp/v.mp4",
        clips=["c1", "c2"],
        clip_seconds=10,
        aspect="9:16",
        title="Test video",
        video_url="http://example.com/v.mp4",
        thumbnail_path=None,
        thumbnail_url=None,
    )
    mock_fanpage = SimpleNamespace(
        id=2,
        qa_ai_video_enabled=False,
        ai_video_logo_enabled=False,
        ai_video_captions_enabled=False,
    )
    mock_idea = MagicMock(variables={})
    mock_settings = SimpleNamespace(id=1, ai_video_vision_qc_enabled=True)

    def filter_by_side_effect(**kwargs):
        query_mock = MagicMock()
        if "id" in kwargs:
            obj_id = kwargs["id"]
            if obj_id == 55:
                query_mock.first.return_value = mock_job
            elif obj_id == 1:
                # VideoIdea or Settings
                query_mock.first.return_value = mock_idea
            elif obj_id == 2:
                query_mock.first.return_value = mock_fanpage
        return query_mock

    # Setup query dispatch for the models
    def query_dispatch(model):
        query_mock = MagicMock()
        model_name = getattr(model, "__name__", str(model))
        if "VideoJob" in model_name:
            query_mock.filter_by.return_value.first.return_value = mock_job
        elif "VideoIdea" in model_name:
            query_mock.filter_by.return_value.first.return_value = mock_idea
        elif "TargetFanpage" in model_name:
            query_mock.filter_by.return_value.first.return_value = mock_fanpage
        elif "Settings" in model_name:
            query_mock.filter_by.return_value.first.return_value = mock_settings
        return query_mock

    mock_db.query.side_effect = query_dispatch
    mock_tech.return_value = {"passed": True, "reasons": []}

    with patch("app.services.ai_video_qc.vision_check") as mock_vision, \
         patch("app.tasks.ai_video_finalize.SessionLocal", return_value=mock_db):
        mock_vision.side_effect = AssertionError("vision_check must not be called when qa_ai_video_enabled is False")
        
        # Act
        finalize_ai_video(55)

        # Assert
        mock_vision.assert_not_called()
        assert mock_job.qc["passed"] is True
        assert mock_job.qc["vision"] is None
