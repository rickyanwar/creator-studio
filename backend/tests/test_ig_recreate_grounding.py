from unittest.mock import MagicMock, patch
from app.tasks.ig_recreate import render_ig_recreate
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.design_templates import DesignTemplate
from app.models.radar import RadarStoryDecision

@patch("app.tasks.ig_recreate.SessionLocal")
@patch("app.services.design_images.source_news_main")
@patch("app.services.design_images.prepare_design_images")
@patch("app.services.design_images.focus_points_for")
@patch("app.services.design_images.watermark_datauri")
@patch("app.services.design_images._safe_face_cy_ceiling")
@patch("httpx.post")
@patch("builtins.open")
@patch("pathlib.Path.mkdir")
@patch("pathlib.Path.write_bytes")
@patch("app.tasks.publisher.publish_job")
def test_grounding_uses_pre_analysis(
    mock_publish, mock_write_bytes, mock_mkdir, mock_open, mock_httpx_post, mock_safe_face, mock_watermark, mock_focus, mock_prepare, mock_source, mock_session
):
    db = MagicMock()
    mock_session.return_value = db
    
    post = Post(id=1, image_local_paths=["/source.jpg"], ig_source_id=1)
    fanpage = TargetFanpage(id=1, visual_engine="off", publish_mode="auto", name="fp")
    template = DesignTemplate(id=1, template_json="{}", canvas_width=100, canvas_height=100, category="quote")
    
    analysis_json = {
        "type": "quote",
        "text": "It was a tough race",
        "speaker": "Lando Norris",
        "main_subject": "Lando Norris",
        "secondary_kind": "none",
        "secondary": "",
        "inset_context": "rain",
        "inset_query": "",
        "moment_summary": "",
        "radio_lines": [],
        "weather": "none",
        "mood": "neutral",
        "people": [],
        "unique_moment": False
    }
    
    job = PublishJob(
        id=1, post_id=1, fanpage_id=1, design_template_id=1, 
        status=PublishJobStatus.pending_design, content_type=ContentType.ig_recreate, 
        design_title='"It was a tough race"',
        design_analysis_json=analysis_json
    )
    decision = RadarStoryDecision(id=1, publish_job_id=1, story_id=1, fanpage_id=1)
    
    def side_effect(model):
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
        elif model == RadarStoryDecision:
            m.filter_by.return_value.first.return_value = decision
        else:
            m.filter_by.return_value.first.return_value = MagicMock(ig_username="test")
        return m
        
    db.query.side_effect = side_effect
    
    mock_open.return_value.__enter__.return_value.read.return_value = b"image_bytes"
    mock_source.return_value = ("data:image/jpeg;base64,...", "/path/to/img.jpg")
    mock_prepare.return_value = ({}, ["data:image/jpeg;base64,..."])
    
    mock_resp = MagicMock()
    mock_resp.content = b"pngdata"
    mock_httpx_post.return_value = mock_resp
    
    render_ig_recreate(1)
    
    # Check that source_news_main was called with the pre_analysis fields, NOT just the title
    mock_source.assert_called_once()
    args, kwargs = mock_source.call_args
    assert args[1] == "Lando Norris rain", f"Expected grounding to use pre_analysis, got {args[1]}"
    assert kwargs.get("known_primary") == "Lando Norris", f"Expected known_primary to be passed"

