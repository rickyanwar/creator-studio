import pytest
from unittest.mock import patch, MagicMock
from app.tasks.design_renderer import render_design
from app.models.publish_jobs import PublishJob, PublishJobStatus, ContentType
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.design_templates import DesignTemplate

@patch('app.tasks.design_renderer.SessionLocal')
@patch('app.services.design_images.source_news_main')
@patch('app.tasks.design_renderer.httpx.post')
@patch('app.tasks.design_renderer.Path')
def test_render_design_news_mode2(mock_path, mock_post, mock_source, mock_session_cls):
    mock_db = MagicMock()
    mock_session_cls.return_value = mock_db
    
    # Mock source_news_main to return success
    mock_source.return_value = ("data:image/jpeg;base64,mock", "/tmp/mock.jpg")
    
    # Setup job and fanpage for Mode 2
    fp = TargetFanpage(id=1, name="test", publish_mode="manual")
    job = PublishJob(
        id=1, fanpage_id=1, status=PublishJobStatus.pending_design,
        content_type=ContentType.news_content, design_title="title",
        source_article_id=1
    )
    job.last_image_marker = "gallery:123"
    template = DesignTemplate(id=1, template_json='{"objects": [{"placeholderRole": "image"}]}', canvas_width=1080, canvas_height=1080)
    article = MagicMock()
    article.scraped_title = "title"

    def mock_query_filter(model):
        m = MagicMock()
        if getattr(model, "__name__", "") == "PublishJob":
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        elif getattr(model, "__name__", "") == "TargetFanpage":
            m.filter_by.return_value.first.return_value = fp
            return m
        elif getattr(model, "__name__", "") == "DesignTemplate":
            m.filter_by.return_value.first.return_value = template
            return m
        elif getattr(model, "__name__", "") == "ScrapedArticle":
            m.filter_by.return_value.first.return_value = article
            return m
        elif "GalleryImage" in str(model):
            m = MagicMock()
            m.filter_by.return_value.scalar.return_value = "/tmp/excluded.jpg"
            m.filter_by.return_value.first.return_value = MagicMock(id=123)
            return m
        return m

    mock_db.query.side_effect = mock_query_filter
    
    mock_resp = MagicMock()
    mock_resp.content = b"fake png"
    mock_post.return_value = mock_resp
    
    render_design(1)
    
    # Check if source_news_main was called
    assert mock_source.called
    kwargs = mock_source.call_args[1]
    assert "exclude_paths" in kwargs
    assert "/tmp/excluded.jpg" in kwargs["exclude_paths"]

@patch('app.tasks.design_renderer.SessionLocal')
@patch('app.services.design_images.source_news_main')
@patch('app.tasks.design_renderer.httpx.post')
@patch('app.tasks.design_renderer.Path')
def test_render_design_discussion_mode4(mock_path, mock_post, mock_source, mock_session_cls):
    mock_db = MagicMock()
    mock_session_cls.return_value = mock_db
    
    # Mock source_news_main to return success
    mock_source.return_value = ("data:image/jpeg;base64,mock", "/tmp/mock.jpg")
    
    fp = TargetFanpage(id=1, name="test", publish_mode="manual")
    job = PublishJob(
        id=2, fanpage_id=1, status=PublishJobStatus.pending_design,
        content_type=ContentType.discussion, design_title="title",
        source_article_id=2
    )
    job.last_image_marker = "gallery:123"
    template = DesignTemplate(id=1, template_json='{"objects": [{"placeholderRole": "image"}]}', canvas_width=1080, canvas_height=1080)
    article = MagicMock()
    article.scraped_title = "title"

    def mock_query_filter(model):
        m = MagicMock()
        if getattr(model, "__name__", "") == "PublishJob":
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
            return m
        elif getattr(model, "__name__", "") == "TargetFanpage":
            m.filter_by.return_value.first.return_value = fp
            return m
        elif getattr(model, "__name__", "") == "DesignTemplate":
            m.filter_by.return_value.first.return_value = template
            return m
        elif getattr(model, "__name__", "") == "ScrapedArticle":
            m.filter_by.return_value.first.return_value = article
            return m
        elif "GalleryImage" in str(model):
            m = MagicMock()
            m.filter_by.return_value.scalar.return_value = "/tmp/excluded.jpg"
            m.filter_by.return_value.first.return_value = MagicMock(id=123)
            return m
        return m

    mock_db.query.side_effect = mock_query_filter
    
    mock_resp = MagicMock()
    mock_resp.content = b"fake png"
    mock_post.return_value = mock_resp
    
    render_design(2)
    
    # Check if source_news_main was called
    assert mock_source.called
    kwargs = mock_source.call_args[1]
    assert "exclude_paths" in kwargs
    assert "/tmp/excluded.jpg" in kwargs["exclude_paths"]

