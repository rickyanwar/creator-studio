"""Tests for pro poster fallback policy in design_renderer and pro_poster_eval."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.models.publish_jobs import PublishJobStatus
from app.scripts.pro_poster_eval import determine_eval_final, run_eval
from app.services.pro_poster.types import (
    ArtDirection,
    EventContext,
    PhotoSelection,
    PosterBrief,
    ProPosterResult,
    QAResult,
    Subject,
)
from app.tasks.design_renderer import render_design


def _make_brief(job_id: int = 1, badge_label: str = "NEWS") -> PosterBrief:
    return PosterBrief(
        job_id=job_id,
        subjects=(Subject(name="Max Verstappen", role="hero"),),
        event=EventContext(series="Formula 1", vehicle="Formula 1 car", circuit="Monza"),
        story_type="result",
        mood="happy",
        badge_label=badge_label,
        accent_colors=("#FF8000",),
    )


def _setup_render_design_mocks(job_id: int = 203):
    s = MagicMock(pro_poster_enabled=True, pro_poster_fanpage_ids=[])
    job = MagicMock(
        id=job_id,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_subtitle="Sub",
        design_caption="Cap",
        design_analysis_json={},
        is_breaking=False,
        last_image_marker=None,
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

    db = MagicMock()

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
    return db, job, fanpage, article, template


# ── 1. Renderer block tests ───────────────────────────────────────────────────


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.services.design_images.resolve_template")
def test_renderer_pro_poster_passed_uses_pro_poster_marker(
    mock_resolve, mock_session, mock_prepare, mock_post
):
    """When pro poster passes with image, image_marker is 'pro_poster'."""
    db, job, fanpage, article, template = _setup_render_design_mocks(203)
    mock_session.return_value = db
    mock_resolve.return_value = template
    mock_prepare.return_value = ({"objects": []}, ["img1"])
    mock_post.return_value.content = b"rendered_card"

    pp_res = ProPosterResult(
        image=b"valid_pro_poster_png",
        brief=_make_brief(203, badge_label="F1 NEWS"),
        selection=PhotoSelection(hero=None, context=(), rejected=()),
        direction=ArtDirection(layout="hero_with_context", prompt="p", refs=()),
        qa=QAResult(passed=True, scores={"event": 5, "ai_look": 5}, issues=()),
        engine="gptcli",
        attempts=1,
        fallback_reason=None,
    )

    with patch("app.services.pro_poster.pipeline.compose_pro_poster", return_value=pp_res), \
         patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(203)

    assert job.last_image_marker == "pro_poster"
    assert job.design_engine == "gptcli"
    assert "pro poster · qa event:5 ai_look:5" in job.design_engine_note
    assert job.design_analysis_json["pro_poster_qa"]["passed"] is True


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.services.design_images.resolve_template")
def test_renderer_pro_poster_no_verified_photo_uses_typo_marker(
    mock_resolve, mock_session, mock_prepare, mock_post
):
    """When image is None and fallback_reason is 'no verified photo', marker is 'pro_poster_typo'."""
    db, job, fanpage, article, template = _setup_render_design_mocks(203)
    mock_session.return_value = db
    mock_resolve.return_value = template
    mock_prepare.return_value = ({"objects": []}, ["img1"])
    mock_post.return_value.content = b"rendered_card"

    pp_res = ProPosterResult(
        image=None,
        brief=_make_brief(203, badge_label="GT NEWS"),
        selection=PhotoSelection(hero=None, context=(), rejected=()),
        direction=ArtDirection(layout="typographic", prompt="", refs=()),
        qa=None,
        engine=None,
        attempts=0,
        fallback_reason="no verified photo",
    )

    with patch("app.services.pro_poster.pipeline.compose_pro_poster", return_value=pp_res), \
         patch("app.services.pro_poster.typo_background.make_typographic_background", return_value=b"fake_bg_png") as mock_tb, \
         patch("app.services.pro_poster.typo_background.background_datauri", return_value="data:image/png;base64,BG") as mock_uri, \
         patch("app.services.design_images.single_photo_face_fits") as mock_fits, \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"):
        render_design(203)

    mock_tb.assert_called_once()
    mock_uri.assert_called_once_with(b"fake_bg_png")
    mock_fits.assert_not_called()
    assert job.last_image_marker == "pro_poster_typo"
    assert job.design_engine == "template"
    assert job.design_engine_note == "no verified photo"


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.services.design_images.resolve_template")
def test_renderer_pro_poster_qa_failed_falls_back_to_old_path(
    mock_resolve, mock_session, mock_prepare, mock_post
):
    """When image is None and fallback_reason is QA failure, falls back to old path and does NOT use typo."""
    db, job, fanpage, article, template = _setup_render_design_mocks(203)
    mock_session.return_value = db
    mock_resolve.return_value = template
    mock_prepare.return_value = ({"objects": []}, ["img1"])
    mock_post.return_value.content = b"rendered_card"

    pp_res = ProPosterResult(
        image=None,
        brief=_make_brief(203, badge_label="UFC NEWS"),
        selection=PhotoSelection(hero=None, context=(), rejected=()),
        direction=ArtDirection(layout="split", prompt="prompt", refs=()),
        qa=QAResult(passed=False, scores={"event": 2, "ai_look": 5}, issues=("wrong fighter",)),
        engine="gptcli",
        attempts=2,
        fallback_reason="qa: event 2 < 4",
    )

    with patch("app.services.pro_poster.pipeline.compose_pro_poster", return_value=pp_res), \
         patch("app.services.pro_poster.typo_background.make_typographic_background") as mock_tb, \
         patch("app.tasks.design_renderer._maybe_compose_speaker_design", return_value=None) as mock_speaker, \
         patch("app.tasks.design_renderer.select_image_for_job", return_value=("data:image/jpeg;base64,/9j/4AAQ", MagicMock(local_path="/p"), "article_hero")) as mock_select, \
         patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("pathlib.Path.write_bytes"), \
         patch("app.tasks.design_renderer.logger.info") as mock_log_info:
        render_design(203)

    # Typographic background was NOT called
    mock_tb.assert_not_called()
    # Old path speaker and candidate selectors were called
    mock_speaker.assert_called_once()
    mock_select.assert_called_once()
    # Info log emitted with exact wording
    mock_log_info.assert_any_call("Pro poster job %d fell back to the old path: %s", 203, "qa: event 2 < 4")
    # Marker is from old path (article_hero), not pro_poster_typo
    assert job.last_image_marker == "article_hero"
    assert job.last_image_marker != "pro_poster_typo"
    # QA was preserved in analysis json
    assert job.design_analysis_json["pro_poster_qa"]["passed"] is False
    assert job.design_analysis_json["pro_poster_qa"]["scores"] == {"event": 2, "ai_look": 5}


# ── 2. Eval script helper & logic tests ───────────────────────────────────────


def test_determine_eval_final_helper():
    """Unit test extracted determine_eval_final helper without DB."""
    eval_url = "/media/pro_poster_eval/test_run/100_new.png"
    live_url = "http://media/live.png"

    # Case 1: Pro poster image exists -> pro_poster, eval_url
    final, new_url = determine_eval_final(b"valid_png", None, eval_url, live_url)
    assert final == "pro_poster"
    assert new_url == eval_url

    # Case 2: Image is None + 'no verified photo' -> typographic, eval_url
    final, new_url = determine_eval_final(None, "no verified photo", eval_url, live_url)
    assert final == "typographic"
    assert new_url == eval_url

    # Case 3: Image is None + QA failure -> production, live_url
    final, new_url = determine_eval_final(None, "qa: event 2 < 4", eval_url, live_url)
    assert final == "production"
    assert new_url == live_url

    # Case 4: Image is None + engine failure -> production, live_url
    final, new_url = determine_eval_final(None, "engine error: timeout", eval_url, live_url)
    assert final == "production"
    assert new_url == live_url


def test_eval_script_production_fallback_does_not_render(tmp_path, monkeypatch):
    """When pro poster QA fails in eval, does NOT render card, sets final='production' and new_url=live_url."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = MagicMock(
        id=12699,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_image_url="http://media/live_photo_card.png",
    )
    fanpage = MagicMock(id=1, name="Motorsport")
    article = MagicMock(id=1, scraped_title="Scraped")
    template = MagicMock(id=1, category="news")

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

    pp_res = ProPosterResult(
        image=None,
        brief=_make_brief(12699),
        selection=PhotoSelection(hero=None, context=(), rejected=()),
        direction=ArtDirection(layout="typographic", prompt="", refs=()),
        qa=QAResult(passed=False, scores={"ai_look": 2}, issues=("waxy",)),
        engine="gptcli",
        attempts=2,
        fallback_reason="qa: ai_look 2 < 4",
    )

    with patch("app.scripts.pro_poster_eval.SessionLocal", return_value=db), \
         patch("app.scripts.pro_poster_eval.compose_pro_poster", return_value=pp_res), \
         patch("app.scripts.pro_poster_eval.render_card_png") as mock_render, \
         patch("app.services.pro_poster.typo_background.make_typographic_background") as mock_tb:
        run_eval([12699], "test_prod_fallback", pace=0)

    # Typographic background and render_card_png must NOT be called
    mock_tb.assert_not_called()
    mock_render.assert_not_called()

    run_dir = tmp_path / "pro_poster_eval" / "test_prod_fallback"
    assert not (run_dir / "12699_new.png").exists()

    index_data = json.loads((run_dir / "index.json").read_text())
    assert len(index_data) == 1
    assert index_data[0]["final"] == "production"
    assert index_data[0]["new_url"] == "http://media/live_photo_card.png"
    assert index_data[0]["fallback_reason"] == "qa: ai_look 2 < 4"


def test_eval_script_fresh_brief_flag(tmp_path, monkeypatch):
    """When fresh_brief=True, in-memory job.design_analysis_json loses pro_poster_brief before compose_pro_poster."""
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    cached_brief = {"badge_label": "OLD LABEL", "headline": "OLD HEADLINE"}
    job = MagicMock(
        id=12700,
        fanpage_id=1,
        source_article_id=1,
        design_title="Title",
        design_image_url="http://media/live.png",
        design_analysis_json={"pro_poster_brief": cached_brief, "other_key": 123},
    )
    fanpage = MagicMock(id=1, name="Motorsport")
    article = MagicMock(id=1, scraped_title="Scraped")
    template = MagicMock(id=1, category="news")

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

    captured_job_analysis = []

    def mock_compose(db_, fp_, job_, art_, dry_run=True):
        captured_job_analysis.append(dict(job_.design_analysis_json))
        return ProPosterResult(
            image=None,
            brief=_make_brief(12700),
            selection=PhotoSelection(hero=None, context=(), rejected=()),
            direction=ArtDirection(layout="typographic", prompt="", refs=()),
            qa=None,
            engine=None,
            attempts=0,
            fallback_reason="no verified photo",
        )

    with patch("app.scripts.pro_poster_eval.SessionLocal", return_value=db), \
         patch("app.scripts.pro_poster_eval.compose_pro_poster", side_effect=mock_compose), \
         patch("app.services.pro_poster.typo_background.make_typographic_background", return_value=b"bg"), \
         patch("app.services.pro_poster.typo_background.background_datauri", return_value="data:bg"), \
         patch("app.scripts.pro_poster_eval.render_card_png", return_value=b"card"):
        run_eval([12700], "test_fresh_brief", pace=0, fresh_brief=True)

    assert len(captured_job_analysis) == 1
    # pro_poster_brief was stripped
    assert "pro_poster_brief" not in captured_job_analysis[0]
    assert captured_job_analysis[0]["other_key"] == 123

    run_dir = tmp_path / "pro_poster_eval" / "test_fresh_brief"
    index_data = json.loads((run_dir / "index.json").read_text())
    assert index_data[0]["final"] == "typographic"
