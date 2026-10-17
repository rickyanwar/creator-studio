from unittest.mock import MagicMock, mock_open, patch
import pytest

from app.tasks.design_renderer import render_design
from app.tasks.ig_recreate import render_ig_recreate


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.services.design_images.resolve_template")
def test_render_design_payload_carries_accent(mock_resolve, mock_select, mock_session, mock_prepare, mock_post):
    db = MagicMock()
    mock_session.return_value = db

    mock_job = MagicMock()
    mock_job.id = 1
    mock_job.design_title = "Title"
    mock_job.design_subtitle = "Subtitle"
    mock_job.design_caption = "Caption"
    mock_job.fanpage_id = 1
    mock_job.design_template_id = 1
    mock_job.is_breaking = False

    mock_fanpage = MagicMock(name="Fanpage")
    mock_fanpage.accent_color = "#FFC400"
    mock_fanpage.watermark_text = ""
    mock_fanpage.watermark_image_url = None

    mock_resolve.return_value = MagicMock(
        template_json={"objects": [{"placeholderRole": "title"}]},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    call_count = 0
    def fake_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1: return mock_job
        if call_count == 2: return mock_fanpage
        if call_count == 3: return MagicMock(scraped_title="Art")
        return mock_job

    db.query.return_value.filter.return_value.update.return_value = 1
    db.query.return_value.filter_by.return_value.first.side_effect = fake_first

    mock_select.return_value = ("data:image/png;base64,123", None, "marker")
    tj = {"objects": [{"placeholderRole": "title"}]}
    mock_prepare.return_value = (tj, ["img1"])
    mock_post.return_value.content = b"pngdata"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open(read_data=b"123")), \
         patch("pathlib.Path.write_bytes"):
        render_design(1)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    assert payload.get("accent") == "#FFC400"
    assert "look" in payload
    assert payload.get("look") is None


@patch("app.tasks.ig_recreate.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.ig_recreate.SessionLocal")
@patch("app.services.design_images.source_news_main")
@patch("app.services.design_images.resolve_template")
def test_render_ig_recreate_payload_carries_accent(mock_resolve, mock_source, mock_session, mock_prepare, mock_post):
    db = MagicMock()
    mock_session.return_value = db

    mock_job = MagicMock()
    mock_job.id = 1
    mock_job.design_title = "Title"
    mock_job.design_subtitle = "Subtitle"
    mock_job.design_caption = "Caption"
    mock_job.design_template_id = 1
    mock_job.post_id = 1
    mock_job.fanpage_id = 1

    mock_fanpage = MagicMock(name="Fanpage", visual_engine="off", design_expand=False)
    mock_fanpage.accent_color = "#2F80ED"
    mock_fanpage.watermark_text = ""
    mock_fanpage.watermark_image_url = None

    mock_resolve.return_value = MagicMock(
        template_json={"objects": [{"placeholderRole": "title"}]},
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    call_count = 0
    def fake_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1: return mock_job
        if call_count == 2: return mock_fanpage
        if call_count == 3: return MagicMock(image_local_paths=["path"])
        if call_count == 4: return MagicMock(
            template_json={"objects": [{"placeholderRole": "title"}]},
            category="news", canvas_width=1080, canvas_height=1350,
        )
        return None

    db.query.return_value.filter.return_value.update.return_value = 1
    db.query.return_value.filter_by.return_value.first.side_effect = fake_first

    mock_source.return_value = ("data:image/png;base64,123", "path")
    tj = {"objects": [{"placeholderRole": "title"}]}
    mock_prepare.return_value = (tj, ["img1"])
    mock_post.return_value.content = b"pngdata"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open(read_data=b"123")), \
         patch("pathlib.Path.write_bytes"):
        render_ig_recreate(1)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    assert payload.get("accent") == "#2F80ED"
    assert "look" in payload
    assert payload.get("look") is None


@patch("app.tasks.design_renderer.httpx.post")
@patch("app.services.design_images.prepare_design_images")
@patch("app.tasks.design_renderer.SessionLocal")
@patch("app.tasks.design_renderer.select_image_for_job")
@patch("app.services.design_images.resolve_template")
def test_render_design_payload_carries_look_following_template(mock_resolve, mock_select, mock_session, mock_prepare, mock_post):
    db = MagicMock()
    mock_session.return_value = db

    mock_job = MagicMock()
    mock_job.id = 1
    mock_job.design_title = "Title"
    mock_job.design_subtitle = "Subtitle"
    mock_job.design_caption = "Caption"
    mock_job.fanpage_id = 1
    mock_job.design_template_id = 1
    mock_job.is_breaking = False

    mock_fanpage = MagicMock(name="Fanpage")
    mock_fanpage.brand_override = True
    mock_fanpage.accent_color = "#2563EB"
    mock_fanpage.accent_secondary_color = "#FFC21A"
    mock_fanpage.badge_color = None
    mock_fanpage.badge_text_color = None
    mock_fanpage.quote_mark_color = None
    mock_fanpage.quote_streak = None
    mock_fanpage.watermark_text = ""
    mock_fanpage.watermark_image_url = None

    mock_resolve.return_value = MagicMock(
        template_json={
            "objects": [{"placeholderRole": "title"}],
            "accentFollowsFanpage": True,
            "brandDefaults": {"primary": "#C7F754"},
        },
        canvas_width=1080,
        canvas_height=1350,
        category="news",
    )

    call_count = 0
    def fake_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1: return mock_job
        if call_count == 2: return mock_fanpage
        if call_count == 3: return MagicMock(scraped_title="Art")
        return mock_job

    db.query.return_value.filter.return_value.update.return_value = 1
    db.query.return_value.filter_by.return_value.first.side_effect = fake_first

    mock_select.return_value = ("data:image/png;base64,123", None, "marker")
    tj = {
        "objects": [{"placeholderRole": "title"}],
        "accentFollowsFanpage": True,
        "brandDefaults": {"primary": "#C7F754"},
    }
    mock_prepare.return_value = (tj, ["img1"])
    mock_post.return_value.content = b"pngdata"

    with patch("app.services.design_images.single_photo_face_fits", return_value=True), \
         patch("app.services.design_images.focus_points_for", return_value=[]), \
         patch("pathlib.Path.mkdir"), \
         patch("builtins.open", mock_open(read_data=b"123")), \
         patch("pathlib.Path.write_bytes"):
        render_design(1)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    assert payload.get("accent") == "#2563EB"
    assert payload.get("look") == {
        "primary": "#2563EB",
        "secondary": "#FFC21A",
        "badge": "#2563EB",
        "badgeText": "#FFFFFF",
        "quoteMarks": "#2563EB",
        "streak": True,
    }
