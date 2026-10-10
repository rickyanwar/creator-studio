import os
import pytest
from unittest.mock import patch, MagicMock

from app.services.redesign_pipeline import redesign_card, RedesignResult
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.radar import RadarStoryDecision
from app.models.design_templates import DesignTemplate
from app.services.visual_engine import VisualEngineError
from app.tasks.ig_recreate import render_ig_recreate

@pytest.fixture
def dummy_deps():
    post = Post(id=1, image_local_paths=["/source.jpg"], ig_source_id=1)
    fanpage = TargetFanpage(id=1, radar_niches=["F1"], visual_engine="chatgpt", ig_recreate_enabled=True)
    return post, fanpage

@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate")
@patch("app.services.redesign_pipeline.check_generated")
@patch("builtins.open")
def test_redesign_card_returns_false_on_qa_fail(
    mock_open, mock_check, mock_generate, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    """Test that redesign_card itself returns ok=False when QA fails twice."""
    post, fanpage = dummy_deps
    
    analysis = MagicMock(type="team_radio", main_subject="Max", radio_lines=[])
    mock_analyze.return_value = analysis
    
    mock_layout.return_value = MagicMock(layout="team_radio")
    
    mock_plan_res = MagicMock()
    mock_plan_res.hero = MagicMock(path="/hero.jpg", gallery_id=1)
    mock_plan_res.inset = None
    mock_plan_res.notes = []
    mock_car = MagicMock(path="/car.jpg")
    mock_car.gallery_id = 2
    mock_plan_res.extra = [mock_car]
    mock_plan.return_value = mock_plan_res
    
    driver = MagicMock(name="Max", number="1", colour_hex="#000", team="RBR")
    mock_driver.return_value = driver
    
    # generate succeeds but QA fails twice
    mock_generate.return_value = b"gen"
    mock_check.side_effect = [
        MagicMock(passed=False, problems=["Bad1"]),
        MagicMock(passed=False, problems=["Bad2"])
    ]
    
    db = MagicMock()
    mock_open.return_value.__enter__.return_value.read.return_value = b"source_image"

    res = redesign_card(db, post=post, fanpage=fanpage, job=None, decision=None)
    
    assert res.ok is False
    assert "Bad2" in res.problems or "Bad1" in res.problems
    assert mock_generate.call_count == 2
    assert mock_check.call_count == 2

@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate")
@patch("builtins.open")
def test_redesign_card_returns_false_on_visual_engine_error(
    mock_open, mock_generate, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    """Test that redesign_card itself returns ok=False on VisualEngineError."""
    post, fanpage = dummy_deps
    
    analysis = MagicMock(type="news", main_subject="Max", radio_lines=[])
    mock_analyze.return_value = analysis
    mock_layout.return_value = MagicMock(layout="inset")
    
    mock_plan_res = MagicMock()
    mock_plan_res.hero = MagicMock(path="/hero.jpg", gallery_id=1)
    mock_plan_res.inset = None
    mock_plan_res.notes = []
    mock_plan_res.extra = []
    mock_plan.return_value = mock_plan_res
    
    mock_generate.side_effect = VisualEngineError("quota", "quota exhausted")
    
    db = MagicMock()
    mock_open.return_value.__enter__.return_value.read.return_value = b"source_image"

    res = redesign_card(db, post=post, fanpage=fanpage, job=None, decision=None)
    
    assert res.ok is False
    assert any("quota exhausted" in p for p in res.problems)
    assert mock_generate.call_count == 1


@patch("app.tasks.ig_recreate.SessionLocal")
@patch("app.services.redesign_pipeline.redesign_card")
@patch("app.services.design_images.source_news_main")
@patch("app.services.design_images.prepare_design_images")
@patch("app.services.design_images.focus_points_for")
@patch("app.services.design_images.watermark_datauri")
@patch("app.services.design_images._safe_face_cy_ceiling")
@patch("httpx.post")
@patch("builtins.open")
@patch("pathlib.Path.mkdir")
@patch("pathlib.Path.write_bytes")
@patch("os.makedirs")
@patch("app.tasks.publisher.publish_job")
def test_render_ig_recreate_fallback_mode3(
    mock_publish, mock_makedirs, mock_write_bytes, mock_mkdir, mock_open, mock_httpx_post, mock_safe_face, mock_watermark, mock_focus, mock_prepare, mock_source, mock_redesign, mock_session
):
    """Test that render_ig_recreate falls back to Mode 3 when redesign_card fails for non-'other'."""
    db = MagicMock()
    mock_session.return_value = db
    
    post = Post(id=1, image_local_paths=["/source.jpg"], ig_source_id=1)
    fanpage = TargetFanpage(id=1, visual_engine="chatgpt", publish_mode="auto", name="fp")
    template = DesignTemplate(id=1, template_json="{}", canvas_width=100, canvas_height=100, category="quote")
    job = PublishJob(id=1, post_id=1, fanpage_id=1, design_template_id=1, status=PublishJobStatus.pending_design, content_type=ContentType.ig_recreate, design_title="Non-empty title")
    decision = RadarStoryDecision(id=1, publish_job_id=1, story_id=1, fanpage_id=1)
    
    def mock_filter_by(**kwargs):
        m = MagicMock()
        if "id" in kwargs:
            if kwargs["id"] == 1:
                # Return the right object based on what was queried
                pass
        return m
        
    # We need to mock db.query to return our mocked objects
    def db_query_mock(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fanpage
        elif model == Post:
            m.filter_by.return_value.first.return_value = post
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        elif model.__name__ == 'RadarStoryDecision':
            m.filter_by.return_value.first.return_value = decision
        else:
            m.filter_by.return_value.first.return_value = None
        return m
    db.query = db_query_mock

    # Redesign fails (e.g. QA fail or Engine Error)
    mock_redesign.return_value = RedesignResult(
        ok=False, layout="team_radio", image_path=None, image_url=None, 
        final_text_rendered_by="template", notes=[], problems=["QA failed"], content_type="team_radio"
    )
    
    mock_source.return_value = ("data:image/jpeg;base64,abc", "/new_photo.jpg")
    mock_prepare.return_value = ({}, ["data:image/jpeg;base64,abc"])
    
    mock_resp = MagicMock()
    mock_resp.content = b"rendered_fallback"
    mock_httpx_post.return_value = mock_resp
    
    mock_open.return_value.__enter__.return_value.read.return_value = b"source_image"

    render_ig_recreate(job.id)
    
    assert job.status == PublishJobStatus.pending_publish
    assert job.design_image_path is not None
    assert mock_source.call_count == 1
    assert mock_httpx_post.call_count == 1
    assert mock_publish.delay.call_count == 1


@patch("app.tasks.ig_recreate.SessionLocal")
@patch("app.services.redesign_pipeline.redesign_card")
def test_render_ig_recreate_other_failure(mock_redesign, mock_session):
    """Test that 'other' posts fail completely if redesign fails."""
    db = MagicMock()
    mock_session.return_value = db
    
    post = Post(id=1, image_local_paths=["/source.jpg"], ig_source_id=1)
    fanpage = TargetFanpage(id=1, visual_engine="chatgpt", publish_mode="auto", name="fp")
    template = DesignTemplate(id=1, template_json="{}", canvas_width=100, canvas_height=100, category="news")
    job = PublishJob(id=1, post_id=1, fanpage_id=1, design_template_id=1, status=PublishJobStatus.pending_design, content_type=ContentType.ig_recreate, design_title="Non-empty")
    
    def db_query_mock(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fanpage
        elif model == Post:
            m.filter_by.return_value.first.return_value = post
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        else:
            m.filter_by.return_value.first.return_value = None
        return m
    db.query = db_query_mock

    # Redesign fails and it's an 'other' post
    mock_redesign.return_value = RedesignResult(
        ok=False, layout="solo", image_path=None, image_url=None, 
        final_text_rendered_by="template", notes=[], problems=["Engine died"], content_type="other"
    )
    
    render_ig_recreate(job.id)
    
    assert job.status == PublishJobStatus.failed
    assert "Redesign failed:" in job.last_error
