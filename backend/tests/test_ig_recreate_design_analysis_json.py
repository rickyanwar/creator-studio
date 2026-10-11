import pytest
from unittest.mock import patch, MagicMock, mock_open
from app.tasks.ig_recreate import render_ig_recreate

@patch("app.tasks.ig_recreate.SessionLocal")
def test_render_ig_recreate_ignores_repliz_response(mock_session):
    db_mock = MagicMock()
    mock_session.return_value = db_mock

    job_mock = MagicMock()
    job_mock.id = 123
    job_mock.design_title = "Test Title"
    job_mock.design_subtitle = ""
    job_mock.design_caption = ""
    
    # Old job with PostAnalysis inside repliz_response_json, new column is None
    job_mock.repliz_response_json = {"type": "news", "text": "Old Analysis"}
    job_mock.design_analysis_json = None
    
    fanpage_mock = MagicMock()
    fanpage_mock.visual_engine = "standard"
    fanpage_mock.watermark_text = ""
    fanpage_mock.watermark_image_path = None
    fanpage_mock.publish_mode = "manual"
    job_mock.fanpage = fanpage_mock
    job_mock.post = MagicMock()
    job_mock.template_json = {"fake": "json"}
    job_mock.image_local_paths = ["fake.jpg"]
    job_mock.ig_recreate_split_template_id = None
    
    template_mock = MagicMock()
    template_mock.template_json = {"fake": "json"}
    template_mock.canvas_width = 1080
    template_mock.canvas_height = 1080
    
    q1 = MagicMock()
    q2 = MagicMock()
    db_mock.query.return_value = q1
    q1.filter.return_value = q2
    
    def query_side_effect(model):
        m = MagicMock()
        if model.__name__ == 'DesignTemplate':
            m.filter_by.return_value.first.return_value = template_mock
        elif model.__name__ == 'TargetFanpage':
            m.filter_by.return_value.first.return_value = fanpage_mock
        elif model.__name__ == 'PublishJob':
            m.filter_by.return_value.first.return_value = job_mock
            m.filter.return_value.update.return_value = 1
        elif model.__name__ == 'Post':
            p = MagicMock()
            p.image_local_paths = ["fake.jpg"]
            m.filter_by.return_value.first.return_value = p
        else:
            m.filter_by.return_value.first.return_value = None
        return m
        
    db_mock.query.side_effect = query_side_effect
    
    with patch("app.tasks.ig_recreate.httpx.post") as mock_post, \
         patch("app.services.design_images.source_news_main") as mock_source, \
         patch("app.services.design_images.prepare_design_images") as mock_prep, \
         patch("app.services.design_images.focus_points_for") as mock_focus, \
         patch("app.services.design_images.watermark_datauri") as mock_water, \
         patch("builtins.open", mock_open(read_data=b"fake")), \
         patch("app.tasks.ig_recreate.Path"):
         
        mock_source.return_value = (None, None)
        mock_prep.return_value = ({}, [])
        mock_focus.return_value = []
        mock_water.return_value = None
        
        mock_resp = MagicMock()
        mock_resp.content = b"fake"
        mock_resp.raise_for_status = MagicMock()
        mock_post.return_value = mock_resp

        render_ig_recreate(123)
        assert mock_post.called

def test_design_analysis_json_ignores_unknown_keys():
    from app.tasks.ig_recreate import render_ig_recreate
    with patch("app.tasks.ig_recreate.SessionLocal") as mock_session:
        db_mock = MagicMock()
        mock_session.return_value = db_mock

        job_mock = MagicMock()
        job_mock.id = 123
        job_mock.design_title = "Test Title"
        job_mock.design_analysis_json = {
            "type": "news",
            "text": "test",
            "extra_key": "ignore me"
        }
        job_mock.design_subtitle = ""
        job_mock.design_caption = ""

        fanpage_mock = MagicMock()
        fanpage_mock.visual_engine = "standard"
        fanpage_mock.watermark_text = ""
        fanpage_mock.watermark_image_path = None
        fanpage_mock.publish_mode = "manual"
        
        template_mock = MagicMock()
        template_mock.template_json = {"fake": "json"}
        template_mock.canvas_width = 1080
        template_mock.canvas_height = 1080

        def query_side_effect(model):
            m = MagicMock()
            if model.__name__ == 'DesignTemplate':
                m.filter_by.return_value.first.return_value = template_mock
            elif model.__name__ == 'TargetFanpage':
                m.filter_by.return_value.first.return_value = fanpage_mock
            elif model.__name__ == 'PublishJob':
                m.filter_by.return_value.first.return_value = job_mock
                m.filter.return_value.update.return_value = 1
            elif model.__name__ == 'Post':
                p = MagicMock()
                p.image_local_paths = ["fake.jpg"]
                m.filter_by.return_value.first.return_value = p
            else:
                m.filter_by.return_value.first.return_value = None
            return m
            
        db_mock.query.side_effect = query_side_effect

        with patch("app.tasks.ig_recreate.httpx.post") as mock_post, \
             patch("app.services.design_images.source_news_main") as mock_source, \
             patch("app.services.design_images.prepare_design_images") as mock_prep, \
             patch("app.services.design_images.focus_points_for") as mock_focus, \
             patch("app.services.design_images.watermark_datauri") as mock_water, \
             patch("builtins.open", mock_open(read_data=b"fake")), \
             patch("app.tasks.ig_recreate.Path"):

            mock_source.return_value = (None, None)
            mock_prep.return_value = ({}, [])
            mock_focus.return_value = []
            mock_water.return_value = None
            
            mock_resp = MagicMock()
            mock_resp.content = b"fake"
            mock_resp.raise_for_status = MagicMock()
            mock_post.return_value = mock_resp

            render_ig_recreate(123)
            assert mock_post.called
