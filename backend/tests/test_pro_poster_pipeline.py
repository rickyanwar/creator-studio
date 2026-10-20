"""Tests for Pro Poster pipeline orchestration, news integration switch, and eval script."""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services.gptcli_client import GptcliError
from app.services.pro_poster.pipeline import (
    _headline,
    _read_hero_ref_bytes,
    _story_for_photos,
    _story_text,
    compose_pro_poster,
    pro_poster_applies,
)
from app.services.pro_poster.types import (
    ArtDirection,
    EventContext,
    PhotoCandidate,
    PhotoCheck,
    PhotoSelection,
    PosterBrief,
    PosterConcept,
    ProPosterResult,
    QAResult,
    Subject,
)
from app.services.visual_engine import (
    GenerationRequest,
    GenerationResult,
    NoAllowedEngine,
    VisualEngineDeferred,
    VisualEngineError,
)
from app.tasks.design_renderer import render_card_png, render_design


def _make_brief(job_id: int = 1, badge_label: str = "F1 NEWS") -> PosterBrief:
    return PosterBrief(
        job_id=job_id,
        subjects=(Subject(name="Max Verstappen", role="hero"),),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car", circuit="Monza"),
        story_type="result",
        mood="happy",
        badge_label=badge_label,
        accent_colors=("#FF8000",),
    )


def _make_selection(has_hero: bool = True) -> PhotoSelection:
    if not has_hero:
        return PhotoSelection(hero=None, context=(), rejected=())
    chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=42, path_or_url="/tmp/v.jpg", role="hero"),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
    )
    return PhotoSelection(hero=chk, context=(), rejected=())


def _make_direction(layout: str = "hero_with_context") -> ArtDirection:
    return ArtDirection(
        layout=layout,
        prompt="Poster prompt",
        refs=("/tmp/v.jpg",),
    )


# ── A. Pipeline Orchestration Tests ───────────────────────────────────────────


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.pipeline.generate_ex")
def test_compose_typographic_path_no_generation(mock_gen, mock_direct, mock_select):
    """Typographic layout immediately returns without generating an image."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=101, design_analysis_json={})
    article = MagicMock(id=201)

    brief = _make_brief(101)
    selection = _make_selection(has_hero=False)
    mock_select.return_value = selection
    mock_direct.return_value = ArtDirection(layout="typographic", prompt="", refs=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image is None
    assert result.direction.layout == "typographic"
    assert result.attempts == 0
    assert result.fallback_reason == "no verified photo"
    mock_gen.assert_not_called()


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_safety_success_one_generation(mock_finish, mock_gen, mock_safety, mock_direct, mock_select):
    """Successful safety check returns finished image in one generation attempt."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=102, design_analysis_json={})
    article = MagicMock(id=202)

    brief = _make_brief(102)
    selection = _make_selection(has_hero=True)
    direction = _make_direction("hero_with_context")

    mock_select.return_value = selection
    mock_direct.return_value = direction
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True, scores={}, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image == b"finished_png"
    assert result.attempts == 1
    assert result.fallback_reason is None
    assert result.qa.passed is True
    assert mock_gen.call_count == 1
    assert len(result.attempt_log) == 1
    assert result.attempt_log[0]["attempt"] == 1
    assert result.attempt_log[0]["engine"] == "gptcli"
    assert result.attempt_log[0]["qa"]["passed"] is True
    assert result.attempt_log[0]["image"] == b"finished_png"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_safety_failure_no_retry(mock_finish, mock_gen, mock_safety, mock_direct, mock_select):
    """Safety failure results in exactly one generate_ex call (no retry), fallback_reason 'qa: <issue>'."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=103, design_analysis_json={})
    article = MagicMock(id=203)

    brief = _make_brief(103)
    mock_select.return_value = _make_selection(has_hero=True)
    mock_direct.return_value = _make_direction("hero_with_context")

    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=False, issues=("event: shows MotoGP bike instead of F1 car",))

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image is None
    assert result.attempts == 1
    assert result.fallback_reason == "qa: event: shows MotoGP bike instead of F1 car"
    assert mock_gen.call_count == 1
    assert len(result.attempt_log) == 1
    assert result.attempt_log[0]["attempt"] == 1
    assert result.attempt_log[0]["qa"]["passed"] is False
    assert result.attempt_log[0]["qa"]["issues"] == ["event: shows MotoGP bike instead of F1 car"]


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.pipeline.generate_ex")
def test_compose_deferral_propagates(mock_gen, mock_direct, mock_select):
    """VisualEngineDeferred propagates directly out of compose_pro_poster."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=104, design_analysis_json={})
    article = MagicMock(id=204)

    brief = _make_brief(104)
    mock_select.return_value = _make_selection(has_hero=True)
    mock_direct.return_value = _make_direction("hero_with_context")

    until_dt = datetime(2026, 10, 20, 5, 30, tzinfo=timezone.utc)
    mock_gen.side_effect = VisualEngineDeferred(until=until_dt, reason="night budget cap")

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        with pytest.raises(VisualEngineDeferred) as exc_info:
            compose_pro_poster(db, fanpage, job, article)

    assert exc_info.value.reason == "night budget cap"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.pipeline.generate_ex")
def test_compose_engine_error_fallback(mock_gen, mock_direct, mock_select):
    """VisualEngineError or GptcliError turns into fallback_reason='engine: <kind>'."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=105, design_analysis_json={})
    article = MagicMock(id=205)

    brief = _make_brief(105)
    mock_select.return_value = _make_selection(has_hero=True)
    mock_direct.return_value = _make_direction("hero_with_context")

    mock_gen.side_effect = VisualEngineError(kind="quota", message="Quota exceeded")

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image is None
    assert result.attempts == 1
    assert result.fallback_reason == "engine: quota"

    # GptcliError variant
    mock_gen.side_effect = GptcliError(type_="timeout", message="Request timed out")
    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result2 = compose_pro_poster(db, fanpage, job, article)

    assert result2.fallback_reason == "engine: timeout"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.pipeline.generate_ex")
def test_compose_no_allowed_engine_counts_as_no_attempt(mock_gen, mock_direct, mock_select):
    """ChatGPT asleep or cooling down: nothing was generated, so attempts stays 0."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=106, design_analysis_json={})
    article = MagicMock(id=206)

    mock_select.return_value = _make_selection(has_hero=True)
    mock_direct.return_value = _make_direction("hero_with_context")
    mock_gen.side_effect = NoAllowedEngine()

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=_make_brief(106)), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image is None
    assert result.attempts == 0
    assert result.fallback_reason == "engine: chatgpt not available"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_exactly_one_generation_on_failure(mock_finish, mock_gen, mock_safety, mock_direct, mock_select):
    """Pipeline strictly enforces exactly 1 image generation attempt per job (no retry on failure)."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=106, design_analysis_json={})
    article = MagicMock(id=206)

    brief = _make_brief(106)
    mock_select.return_value = _make_selection(has_hero=True)
    mock_direct.return_value = _make_direction("hero_with_context")

    mock_gen.return_value = GenerationResult(image=b"img", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=False, issues=("identity: face mismatch",))

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert mock_gen.call_count == 1
    assert result.attempts == 1
    assert len(result.attempt_log) == 1
    assert result.fallback_reason == "qa: identity: face mismatch"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png_bytes")
def test_compose_single_attempt_log_structure(mock_finish, mock_gen, mock_safety, mock_direct, mock_select):
    """Single successful attempt records exactly one detailed dict in attempt_log."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=107, design_analysis_json={})
    article = MagicMock(id=207)

    brief = _make_brief(107)
    mock_select.return_value = _make_selection(has_hero=True)
    mock_direct.return_value = _make_direction("hero_with_context")

    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True, scores={}, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.attempts == 1
    assert len(result.attempt_log) == 1
    entry = result.attempt_log[0]
    assert entry["attempt"] == 1
    assert entry["engine"] == "gptcli"
    assert entry["qa"] == {
        "passed": True,
        "scores": {},
        "issues": [],
        "fix_hint": "",
    }
    assert entry["image"] == b"finished_png_bytes"
    assert entry["concept"] is None


# ── B. Switch and News Integration Tests ──────────────────────────────────────


def test_pro_poster_applies_logic():
    """pro_poster_applies returns True only when enabled and fanpage is allowed."""
    db = MagicMock()
    s = MagicMock()
    db.query.return_value.first.return_value = s

    # Disabled globally
    s.pro_poster_enabled = False
    s.pro_poster_fanpage_ids = []
    assert pro_poster_applies(db, MagicMock(id=1)) is False

    # Enabled globally, empty list -> applies to all
    s.pro_poster_enabled = True
    s.pro_poster_fanpage_ids = []
    assert pro_poster_applies(db, MagicMock(id=1)) is True
    assert pro_poster_applies(db, MagicMock(id=99)) is True

    # Enabled globally, specific fanpage IDs allowed
    s.pro_poster_fanpage_ids = [1, 2]
    assert pro_poster_applies(db, MagicMock(id=1)) is True
    assert pro_poster_applies(db, MagicMock(id=3)) is False


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.services.design_images.resolve_template")
def test_render_design_switch_off_old_path(mock_resolve, mock_select, mock_session, mock_prepare, mock_post):
    """When switch is OFF, compose_pro_poster is never called and legacy path runs."""
    db = MagicMock()
    mock_session.return_value = db

    s = MagicMock(pro_poster_enabled=False, pro_poster_fanpage_ids=[])
    job = MagicMock(
        id=201,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_subtitle="Sub",
        design_caption="Cap",
        is_breaking=False,
        last_image_marker=None,
    )
    fanpage = MagicMock(id=1, accent_color="#E8362B", watermark_text="", news_visual_engine="off")
    article = MagicMock(id=1, scraped_title="Scraped")
    template = MagicMock(
        id=1,
        template_json={"objects": [{"placeholderRole": "title"}]},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "Settings" in name:
            m.first.return_value = s
            return m
        if "PublishJob" in name:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        if "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
            return m
        if "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
            return m
        if "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
            return m
        return m

    db.query.side_effect = fake_query
    mock_resolve.return_value = template
    mock_select.return_value = ("data:image/png;base64,AAAA", None, "gallery:10")
    mock_prepare.return_value = ({"objects": []}, ["img1"])
    mock_post.return_value.content = b"pngdata"

    with patch("app.services.pro_poster.pipeline.compose_pro_poster") as mock_compose, \
         patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(201)

    mock_compose.assert_not_called()
    assert job.last_image_marker == "gallery:10"
    assert job.design_engine == "template"


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.services.design_images.resolve_template")
def test_render_design_switch_on_image_success(mock_resolve, mock_session, mock_prepare, mock_post):
    """Switch ON + image -> payload label = badge_label, marker = pro_poster, audit notes stored."""
    db = MagicMock()
    mock_session.return_value = db

    s = MagicMock(pro_poster_enabled=True, pro_poster_fanpage_ids=[])
    job = MagicMock(
        id=202,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_subtitle="Sub",
        design_caption="Cap",
        design_analysis_json={},
        is_breaking=False,
    )
    fanpage = MagicMock(id=1, accent_color="#FF8000", watermark_text="", name="F1 FP")
    article = MagicMock(id=1, scraped_title="Scraped")
    template = MagicMock(
        id=1,
        template_json={"objects": [{"placeholderRole": "title"}]},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "Settings" in name:
            m.first.return_value = s
            return m
        if "PublishJob" in name:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        if "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
            return m
        if "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
            return m
        if "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
            return m
        return m

    db.query.side_effect = fake_query
    mock_resolve.return_value = template
    mock_prepare.return_value = ({"objects": []}, ["img1"])
    mock_post.return_value.content = b"pngdata"

    brief = _make_brief(202, badge_label="WORLDSBK NEWS")
    pro_result = ProPosterResult(
        image=b"pro_poster_image_png",
        brief=brief,
        selection=_make_selection(True),
        direction=_make_direction(),
        qa=QAResult(passed=True, scores={"event": 5, "ai_look": 5}, issues=()),
        engine="gptcli",
        attempts=1,
    )

    with patch("app.services.pro_poster.pipeline.compose_pro_poster", return_value=pro_result), \
         patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(202)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    assert payload.get("label") == "WORLDSBK NEWS"
    assert job.last_image_marker == "pro_poster"
    assert job.design_engine == "chatgpt"
    assert "pro poster · qa" in job.design_engine_note
    assert job.design_analysis_json.get("pro_poster_qa") == {
        "scores": {"event": 5, "ai_look": 5},
        "issues": [],
        "passed": True,
    }


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.services.design_images.resolve_template")
def test_render_design_switch_on_no_image_typographic(mock_resolve, mock_session, mock_prepare, mock_post):
    """Switch ON + no image -> typographic background used, no 'needs manual image' error."""
    db = MagicMock()
    mock_session.return_value = db

    s = MagicMock(pro_poster_enabled=True, pro_poster_fanpage_ids=[])
    job = MagicMock(
        id=203,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_subtitle="Sub",
        design_caption="Cap",
        design_analysis_json={},
        is_breaking=False,
    )
    fanpage = MagicMock(id=1, accent_color="#E8362B", watermark_text="", name="GT FP")
    article = MagicMock(id=1, scraped_title="Scraped")
    template = MagicMock(
        id=1,
        template_json={"objects": [{"placeholderRole": "title"}]},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "Settings" in name:
            m.first.return_value = s
            return m
        if "PublishJob" in name:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        if "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
            return m
        if "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
            return m
        if "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
            return m
        return m

    db.query.side_effect = fake_query
    mock_resolve.return_value = template
    mock_prepare.return_value = ({"objects": []}, ["img1"])
    mock_post.return_value.content = b"pngdata"

    brief = _make_brief(203, badge_label="GT WORLD CHALLENGE")
    pro_result = ProPosterResult(
        image=None,
        brief=brief,
        selection=_make_selection(False),
        direction=ArtDirection(layout="typographic", prompt="", refs=()),
        qa=None,
        engine=None,
        attempts=0,
        fallback_reason="no verified photo",
    )

    with patch("app.services.pro_poster.pipeline.compose_pro_poster", return_value=pro_result), \
         patch("app.services.pro_poster.typo_background.make_typographic_background", return_value=b"fake_bg_png") as mock_tb, \
         patch("app.services.pro_poster.typo_background.background_datauri", return_value="data:image/png;base64,BG") as mock_uri, \
         patch("app.services.design_images.single_photo_face_fits") as mock_fits, \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(203)

    mock_tb.assert_called_once()
    mock_uri.assert_called_once_with(b"fake_bg_png")
    # Face fits check is skipped for typographic card
    mock_fits.assert_not_called()
    assert job.last_image_marker == "pro_poster_typo"
    assert job.design_engine == "template"
    assert job.design_engine_note == "no verified photo"
    assert job.last_error is None
    assert job.status == PublishJobStatus.pending_publish


# ── C. Helper & Eval Script Tests ──────────────────────────────────────────────


@patch("app.tasks.design_renderer.render_card_png", return_value=b"custom_png")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.services.design_images.resolve_template")
def test_render_card_png_used_by_render_design(mock_resolve, mock_session, mock_render_card):
    """Verify render_design uses extracted render_card_png helper."""
    db = MagicMock()
    mock_session.return_value = db

    job = MagicMock(id=301, fanpage_id=1, source_article_id=1, design_title="T", design_analysis_json={})
    fanpage = MagicMock(id=1, accent_color="#E8362B")
    article = MagicMock(id=1, scraped_title="S")
    template = MagicMock(id=1, template_json={"objects": []}, canvas_width=1080, canvas_height=1080)

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "Settings" in name:
            m.first.return_value = MagicMock(pro_poster_enabled=False)
            return m
        if "PublishJob" in name:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        if "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
            return m
        if "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
            return m
        if "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
            return m
        return m

    db.query.side_effect = fake_query
    mock_resolve.return_value = template

    with patch("app.tasks.design_renderer.select_image_for_job", return_value=("data:image/png;base64,A", None, "g:1")), \
         patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes") as mock_wb:
        render_design(301)

    assert mock_render_card.called
    mock_wb.assert_called_once_with(b"custom_png")


def test_eval_script_dry_run_and_writes_index(tmp_path, monkeypatch):
    """Eval script never commits to DB (calls rollback) and writes output artifacts & index.json."""
    from app.scripts.pro_poster_eval import run_eval
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = MagicMock(
        id=12602,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_subtitle="Sub",
        design_caption="Cap",
        design_image_url="http://media/old.png",
    )
    fanpage = MagicMock()
    fanpage.id = 1
    fanpage.name = "Test Brand"
    fanpage.accent_color = "#FF8000"
    article = MagicMock(id=1, scraped_title="Scraped Title")
    template = MagicMock(id=1, template_json={"objects": []}, canvas_width=1080, canvas_height=1350)

    def fake_query(model):
        m = MagicMock()
        name = getattr(model, "__name__", str(model))
        if "PublishJob" in name:
            m.filter_by.return_value.first.return_value = job
            return m
        if "TargetFanpage" in name:
            m.filter_by.return_value.first.return_value = fanpage
            return m
        if "ScrapedArticle" in name:
            m.filter_by.return_value.first.return_value = article
            return m
        if "DesignTemplate" in name:
            m.filter_by.return_value.first.return_value = template
            return m
        return m

    db.query.side_effect = fake_query

    brief = _make_brief(12602, badge_label="F1 NEWS")
    pro_result = ProPosterResult(
        image=b"eval_png_bytes",
        brief=brief,
        selection=_make_selection(True),
        direction=_make_direction("hero_with_context"),
        qa=QAResult(passed=True, scores={"event": 5, "ai_look": 5}, issues=()),
        engine="gptcli",
        attempts=2,
        attempt_log=(
            {
                "attempt": 1,
                "engine": "gptcli",
                "qa": {"passed": False, "scores": {"ai_look": 2}, "issues": ["waxy skin"], "fix_hint": "more texture"},
                "image": b"attempt1_bytes",
            },
            {
                "attempt": 2,
                "engine": "gptcli",
                "qa": {"passed": True, "scores": {"event": 5, "ai_look": 5}, "issues": [], "fix_hint": ""},
                "image": b"attempt2_bytes",
            },
        ),
    )

    with patch("app.scripts.pro_poster_eval.SessionLocal", return_value=db), \
         patch("app.scripts.pro_poster_eval.compose_pro_poster", return_value=pro_result) as mock_compose, \
         patch("app.scripts.pro_poster_eval.render_card_png", return_value=b"rendered_png_bytes") as mock_render:
        run_eval([12602], "test_run", pace=0)

    # compose_pro_poster must have dry_run=True
    mock_compose.assert_called_once_with(db, fanpage, job, article, dry_run=True)
    # DB was rolled back, never committed
    assert db.rollback.called
    assert db.commit.called is False

    # Check disk artifacts
    run_dir = tmp_path / "pro_poster_eval" / "test_run"
    assert (run_dir / "12602_new.png").read_bytes() == b"rendered_png_bytes"
    assert (run_dir / "12602_brief.json").exists()
    assert (run_dir / "12602_qa.json").exists()
    index_file = run_dir / "index.json"
    assert index_file.exists()
    index_data = json.loads(index_file.read_text())
    assert len(index_data) == 1
    assert index_data[0]["job_id"] == 12602
    assert index_data[0]["brand"] == "Test Brand"
    assert index_data[0]["engine"] == "gptcli"
    assert index_data[0]["qa"]["passed"] is True
    assert len(index_data[0]["attempts_detail"]) == 2
    assert index_data[0]["attempts_detail"][0]["attempt"] == 1
    assert index_data[0]["attempts_detail"][0]["engine"] == "gptcli"
    assert index_data[0]["attempts_detail"][0]["url"] == "/media/pro_poster_eval/test_run/12602_attempt1.png"
    assert index_data[0]["attempts_detail"][0]["qa"]["passed"] is False
    assert index_data[0]["attempts_detail"][1]["attempt"] == 2
    assert index_data[0]["attempts_detail"][1]["engine"] == "gptcli"
    assert index_data[0]["attempts_detail"][1]["url"] == "/media/pro_poster_eval/test_run/12602_attempt2.png"
    assert index_data[0]["attempts_detail"][1]["qa"]["passed"] is True

    assert (run_dir / "12602_attempt1.png").read_bytes() == b"attempt1_bytes"
    assert (run_dir / "12602_attempt2.png").read_bytes() == b"attempt2_bytes"

    qa_json_data = json.loads((run_dir / "12602_qa.json").read_text())
    assert "attempts_detail" in qa_json_data
    assert len(qa_json_data["attempts_detail"]) == 2


def test_read_hero_ref_bytes_prefers_portrait(tmp_path):
    """_read_hero_ref_bytes prefers portrait, then hero, then face_ref."""
    portrait_file = tmp_path / "portrait.jpg"
    portrait_file.write_bytes(b"portrait_bytes")
    hero_file = tmp_path / "hero.jpg"
    hero_file.write_bytes(b"hero_bytes")
    face_file = tmp_path / "face.jpg"
    face_file.write_bytes(b"face_bytes")

    portrait_chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=1, path_or_url=str(portrait_file), role="hero"),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=5,
        face_frontal=True,
    )
    hero_chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=2, path_or_url=str(hero_file), role="hero"),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
    )
    face_chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=3, path_or_url=str(face_file), role="hero"),
        person="yes",
        event_match="unsure",
        obstructed=False,
        watermark=False,
        quality=4,
    )

    # 1. portrait + hero + face_ref -> portrait preferred
    sel_all = PhotoSelection(hero=hero_chk, face_ref=face_chk, portrait=portrait_chk)
    assert _read_hero_ref_bytes(sel_all) == b"portrait_bytes"

    # 2. hero + face_ref (no portrait) -> hero preferred
    sel_hero = PhotoSelection(hero=hero_chk, face_ref=face_chk, portrait=None)
    assert _read_hero_ref_bytes(sel_hero) == b"hero_bytes"

    # 3. face_ref only -> face_ref used
    sel_face_only = PhotoSelection(hero=None, face_ref=face_chk, portrait=None)
    assert _read_hero_ref_bytes(sel_face_only) == b"face_bytes"

    # 4. Neither present -> None
    sel_neither = PhotoSelection(hero=None, face_ref=None, portrait=None)
    assert _read_hero_ref_bytes(sel_neither) is None


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_portrait_bytes_used_when_given(mock_finish, mock_gen, mock_direct, mock_select, tmp_path):
    """When selection.portrait exists, its bytes are passed to check_safety."""
    portrait_file = tmp_path / "p.jpg"
    portrait_file.write_bytes(b"portrait_bytes_for_safety")
    hero_file = tmp_path / "h.jpg"
    hero_file.write_bytes(b"hero_bytes_other")

    portrait_chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=1, path_or_url=str(portrait_file), role="hero"),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=5,
        face_frontal=True,
    )
    hero_chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=2, path_or_url=str(hero_file), role="hero"),
        person="yes",
        event_match="yes",
        obstructed=False,
        watermark=False,
        quality=4,
    )
    selection = PhotoSelection(hero=hero_chk, portrait=portrait_chk)
    mock_select.return_value = selection
    mock_direct.return_value = _make_direction()
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)

    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=505, design_title="Test", design_analysis_json={})
    article = MagicMock(id=605)
    brief = _make_brief(505)

    captured_hero_ref_bytes = None

    def fake_check_safety(image_bytes, b, *, hero_ref_bytes=None, headline=None):
        nonlocal captured_hero_ref_bytes
        captured_hero_ref_bytes = hero_ref_bytes
        return QAResult(passed=True)

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None), \
         patch("app.services.pro_poster.qa.check_safety", side_effect=fake_check_safety):
        res = compose_pro_poster(db, fanpage, job, article)

    assert res.image == b"finished_png"
    assert captured_hero_ref_bytes == b"portrait_bytes_for_safety"


def test_headline_and_story_text_helpers():
    """_headline and _story_text correctly extract and clean values."""
    job = MagicMock(design_title="**Bulega Tops Test**")
    article = MagicMock(scraped_title="**Bulega News**", scraped_content="Full race report content.")

    # job.design_title preferred, "**" removed
    assert _headline(job, article) == "Bulega Tops Test"

    # fallback to article.scraped_title when job.design_title is None
    job_no_title = MagicMock(design_title=None)
    assert _headline(job_no_title, article) == "Bulega News"

    # both None
    assert _headline(None, None) is None

    # _story_text
    assert _story_text(article) == "Full race report content."
    assert _story_text(None) is None


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_headline_reaches_direct_and_safety(mock_finish, mock_gen, mock_select):
    """headline reaches direct() and check_safety()."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=501, design_title="**Bulega Takes Portimao Pole**", design_analysis_json={})
    article = MagicMock(id=601, scraped_content="Nicolo Bulega shattered the lap record at Portimao.")

    brief = _make_brief(501)
    selection = _make_selection(has_hero=True)
    mock_select.return_value = selection
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)

    direct_kwargs = {}

    def mock_direct(b, sel, *, headline=None, story_text=None, **kwargs):
        direct_kwargs["headline"] = headline
        direct_kwargs["story_text"] = story_text
        return ArtDirection(layout="hero_with_context", prompt="Prompt", refs=("/tmp/v.jpg",))

    safety_kwargs = {}

    def mock_safety(image_bytes, b, *, hero_ref_bytes=None, headline=None):
        safety_kwargs["headline"] = headline
        return QAResult(passed=True, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None), \
         patch("app.services.pro_poster.art_direction.direct", side_effect=mock_direct), \
         patch("app.services.pro_poster.qa.check_safety", side_effect=mock_safety):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image == b"finished_png"
    assert direct_kwargs["headline"] == "Bulega Takes Portimao Pole"
    assert direct_kwargs["story_text"] == "Nicolo Bulega shattered the lap record at Portimao."
    assert safety_kwargs["headline"] == "Bulega Takes Portimao Pole"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_uses_face_ref_when_hero_is_none(mock_finish, mock_gen, mock_select, tmp_path):
    """face_ref is read and passed as hero_ref_bytes to check_safety when hero is None."""
    face_file = tmp_path / "face_ref_img.jpg"
    face_file.write_bytes(b"face_reference_data")

    face_chk = PhotoCheck(
        candidate=PhotoCandidate(source="gallery", gallery_image_id=99, path_or_url=str(face_file), role="hero"),
        person="yes",
        event_match="unsure",
        obstructed=False,
        watermark=False,
        quality=4,
    )
    selection = PhotoSelection(hero=None, face_ref=face_chk, context=())
    mock_select.return_value = selection
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)

    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=502, design_title="Test Title", design_analysis_json={})
    article = MagicMock(id=602, scraped_content="Content")
    brief = _make_brief(502)

    captured_hero_ref_bytes = None

    def mock_safety(image_bytes, b, *, hero_ref_bytes=None, headline=None):
        nonlocal captured_hero_ref_bytes
        captured_hero_ref_bytes = hero_ref_bytes
        return QAResult(passed=True, issues=())

    def mock_direct(b, sel, *, headline=None, story_text=None, **kwargs):
        return ArtDirection(layout="face_ref_scene", prompt="Prompt", refs=(str(face_file),))

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None), \
         patch("app.services.pro_poster.art_direction.direct", side_effect=mock_direct), \
         patch("app.services.pro_poster.qa.check_safety", side_effect=mock_safety):
        result = compose_pro_poster(db, fanpage, job, article)

    assert result.image == b"finished_png"
    assert captured_hero_ref_bytes == b"face_reference_data"


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_pro_poster_lets_router_choose_engine(
    mock_finish, mock_gen, mock_safety, mock_direct, mock_select
):
    """compose_pro_poster does not pass engines to generate_ex, letting router decide."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=701, design_analysis_json={})
    article = MagicMock(id=801)

    brief = _make_brief(701)
    selection = _make_selection(has_hero=True)
    direction = _make_direction("hero_with_context")

    mock_select.return_value = selection
    mock_direct.return_value = direction
    mock_gen.return_value = GenerationResult(image=b"raw1", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        res = compose_pro_poster(db, fanpage, job, article)

    assert res.image == b"finished_png"
    assert mock_gen.call_count == 1
    assert "engines" not in mock_gen.call_args.kwargs


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_pro_poster_codex_result_passes_safety(
    mock_finish, mock_gen, mock_safety, mock_direct, mock_select
):
    """generate_ex returns 9router (Codex) and safety passes -> res.image set, res.engine=='9router', attempts==1."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=701, design_analysis_json={})
    article = MagicMock(id=801)

    brief = _make_brief(701)
    selection = _make_selection(has_hero=True)
    direction = _make_direction("hero_with_context")

    mock_select.return_value = selection
    mock_direct.return_value = direction
    mock_gen.return_value = GenerationResult(image=b"raw1", engine="9router", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        res = compose_pro_poster(db, fanpage, job, article)

    assert res.image is not None
    assert res.image == b"finished_png"
    assert res.engine == "9router"
    assert res.attempts == 1


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.concept.build_concept")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_pro_poster_builds_concept_and_passes_to_direct(
    mock_finish, mock_gen, mock_safety, mock_concept, mock_direct, mock_select
):
    """build_concept is called with story/headline and passed to art_direction.direct."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=702, design_title="Bulega Dominates Jerez", design_analysis_json={})
    article = MagicMock(id=802, scraped_content="Nicolo Bulega won both races at Jerez in commanding fashion.")

    brief = _make_brief(702)
    selection = _make_selection(has_hero=True)
    direction = _make_direction("hero_with_context")

    concept = PosterConcept(
        key_moment="Double victory at Jerez opener",
        scene="Bulega celebrating on the Ducati V4R matching Image 1",
        inset="Trophy ceremony",
        graphic_words=("DOUBLE", "JEREZ"),
        palette="Ducati red and white",
        mood="euphoric",
    )

    mock_select.return_value = selection
    mock_concept.return_value = concept
    mock_direct.return_value = direction
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        res = compose_pro_poster(db, fanpage, job, article)

    assert res.image == b"finished_png"
    mock_concept.assert_called_once_with(
        brief,
        selection,
        headline="Bulega Dominates Jerez",
        story_text="Nicolo Bulega won both races at Jerez in commanding fashion.",
    )
    assert mock_direct.call_args.kwargs.get("concept") == concept
    assert len(res.attempt_log) == 1
    assert res.attempt_log[0]["concept"] == {
        "key_moment": "Double victory at Jerez opener",
        "scene": "Bulega celebrating on the Ducati V4R matching Image 1",
        "inset": "Trophy ceremony",
        "graphic_words": ["DOUBLE", "JEREZ"],
        "palette": "Ducati red and white",
        "mood": "euphoric",
    }


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.concept.build_concept")
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_pro_poster_concept_exception_generates_with_concept_none(
    mock_finish, mock_gen, mock_safety, mock_concept, mock_direct, mock_select
):
    """When build_concept raises an exception, generation still occurs with concept=None."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=703, design_title="Title", design_analysis_json={})
    article = MagicMock(id=803, scraped_content="Content")

    brief = _make_brief(703)
    selection = _make_selection(has_hero=True)
    direction = _make_direction("hero_with_context")

    mock_select.return_value = selection
    mock_concept.side_effect = RuntimeError("Flash model quota exhausted")
    mock_direct.return_value = direction
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True, issues=())

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        res = compose_pro_poster(db, fanpage, job, article)

    assert res.image == b"finished_png"
    assert res.attempts == 1
    assert mock_gen.call_count == 1
    assert mock_direct.call_args.kwargs.get("concept") is None
    assert res.attempt_log[0]["concept"] is None


@patch("app.services.pro_poster.photos.select_photos")
@patch("app.services.pro_poster.art_direction.direct")
@patch("app.services.pro_poster.concept.build_concept", return_value=None)
@patch("app.services.pro_poster.qa.check_safety")
@patch("app.services.pro_poster.pipeline.generate_ex")
@patch("app.services.speaker_design._finish", return_value=b"finished_png")
def test_compose_pro_poster_select_photos_gets_story(
    mock_finish, mock_gen, mock_safety, mock_concept, mock_direct, mock_select
):
    """select_photos is passed story= from _story_for_photos."""
    db = MagicMock()
    fanpage = MagicMock(id=10)
    job = MagicMock(id=704, design_title="Verstappen takes pole", design_analysis_json={})
    article = MagicMock(id=804, scraped_content="A thrilling qualifying session ended with Max on top.")

    brief = _make_brief(704)
    selection = _make_selection(has_hero=True)
    mock_select.return_value = selection
    mock_direct.return_value = _make_direction()
    mock_gen.return_value = GenerationResult(image=b"raw", engine="gptcli", meta={}, event_id=None)
    mock_safety.return_value = QAResult(passed=True)

    with patch("app.services.pro_poster.pipeline.build_brief", return_value=brief), \
         patch("app.services.story_photo.fetch_story_photo", return_value=None):
        compose_pro_poster(db, fanpage, job, article)

    assert mock_select.called
    passed_story = mock_select.call_args.kwargs.get("story")
    assert passed_story == "Verstappen takes pole — A thrilling qualifying session ended with Max on top."


def test_story_for_photos_truncation():
    """_story_for_photos truncates story text to first 600 characters and joins with headline."""
    long_content = "x" * 1000
    res = _story_for_photos("Headline", long_content)
    expected = f"Headline — {'x' * 600}"
    assert res == expected

    # Both empty/None
    assert _story_for_photos(None, None) == ""
    assert _story_for_photos("Only Headline", None) == "Only Headline"
    assert _story_for_photos(None, "Only Story") == "Only Story"

