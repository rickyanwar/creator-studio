import pytest
from unittest.mock import patch, MagicMock

from app.tasks.design_renderer import clean_title_for_render, clear_template_text_objects
from app.tasks.ig_recreate import render_ig_recreate
from app.models.publish_jobs import PublishJobStatus, ContentType
from app.services.redesign_pipeline import redesign_card, RedesignResult
from app.models.publish_jobs import PublishJob
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.radar import RadarStoryDecision
from app.models.design_templates import DesignTemplate

def test_clean_title_for_render_keeps_markdown():
    assert clean_title_for_render("  **ALONSO'S FUTURE:** BEN SULAYEM  ") == "**ALONSO'S FUTURE:** BEN SULAYEM"
    assert clean_title_for_render("__F1 NEWS__") == "__F1 NEWS__"
    assert clean_title_for_render("`quote`") == "`quote`"

def test_clear_template_text_objects_is_pure():
    tj = {
        "objects": [
            {"placeholderRole": "title", "text": "SAMPLE TITLE"},
            {"placeholderRole": "subtitle", "text": "SAMPLE SUBTITLE"},
            {"placeholderRole": "caption", "text": "SAMPLE CAPTION"},
            {"placeholderRole": "label", "text": "SAMPLE LABEL"},
            {"placeholderRole": "other", "text": "OTHER"},
        ]
    }
    tj_new = clear_template_text_objects(tj)
    
    # Check new object
    assert tj_new["objects"][0]["text"] == ""
    assert tj_new["objects"][1]["text"] == ""
    assert tj_new["objects"][2]["text"] == ""
    assert tj_new["objects"][3]["text"] == ""
    assert tj_new["objects"][4]["text"] == "OTHER"
    
    # Check old object was not mutated
    assert tj["objects"][0]["text"] == "SAMPLE TITLE"
    assert tj["objects"][1]["text"] == "SAMPLE SUBTITLE"

@patch("app.tasks.ig_recreate.SessionLocal")
@patch("app.tasks.ig_recreate.httpx.post")
def test_render_ig_recreate_empty_title_fails(mock_post, mock_session):
    db = MagicMock()
    mock_session.return_value = db
    
    post = Post(id=1, image_local_paths=["/source.jpg"], ig_source_id=1)
    fanpage = TargetFanpage(id=1, visual_engine="none", ig_recreate_enabled=True)
    template = DesignTemplate(id=1, template_json="{}", canvas_width=100, canvas_height=100, category="quote")
    job = PublishJob(id=1, post_id=1, fanpage_id=1, design_template_id=1, status=PublishJobStatus.pending_design, content_type=ContentType.ig_recreate, design_title="   ")
    decision = RadarStoryDecision(id=1, publish_job_id=1, story_id=1, fanpage_id=1)

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

    render_ig_recreate(job.id)
    assert job.status == PublishJobStatus.failed
    assert job.last_error == "empty design_title"
    mock_post.assert_not_called()

@patch("httpx.post")
def test_redesign_card_empty_title_fails(mock_post):
    post = Post(id=1, image_local_paths=["/source.jpg"], ig_source_id=1)
    fanpage = TargetFanpage(id=1, radar_niches=["F1"])
    job = PublishJob(id=1, post_id=1, fanpage_id=1, design_title="  ")
    decision = RadarStoryDecision(id=1, publish_job_id=1, story_id=1, fanpage_id=1)
    db = MagicMock()

    with patch("app.services.redesign_pipeline.analyze_ig_post") as mock_analyze:
        mock_analysis = MagicMock()
        mock_analysis.type = "quote"
        mock_analysis.moment_summary = "  "
        mock_analysis.secondary_kind = "none"
        mock_analysis.secondary = ""
        mock_analysis.inset_context = ""
        mock_analysis.inset_query = ""
        mock_analysis.speaker = "Driver A"
        mock_analysis.main_subject = "Driver A"
        mock_analysis.radio_lines = ()
        mock_analysis.unique_moment = False
        mock_analyze.return_value = mock_analysis
        
        with patch("app.services.redesign_pipeline.generate") as mock_generate:
            mock_generate.return_value = b"fake_image_bytes"
            with patch("app.services.redesign_pipeline.check_generated") as mock_check:
                mock_qa = MagicMock()
                mock_qa.passed = True
                mock_qa.problems = []
                mock_check.return_value = mock_qa
                with patch("builtins.open"):
                    result = redesign_card(db, post=post, fanpage=fanpage, job=job, decision=decision)
            
    assert not result.ok
    assert "empty design_title" in result.problems
    assert job.last_error == "empty design_title"
    assert decision.preview_status == "failed"
    mock_post.assert_not_called()

