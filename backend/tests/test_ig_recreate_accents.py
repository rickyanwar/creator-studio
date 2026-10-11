import pytest
from unittest.mock import patch, MagicMock, mock_open
from app.tasks.design_renderer import render_design
from app.tasks.ig_recreate import render_ig_recreate, _build_quote_attribution


# ── Mode 2 (render_design) — template_json must NOT be mutated ────────────────

@patch('app.tasks.design_renderer.httpx.post')
@patch('app.services.design_images.prepare_design_images')
@patch('app.tasks.design_renderer.SessionLocal')
@patch('app.tasks.design_renderer.select_image_for_job')
@patch('app.services.design_images.resolve_template')
def test_design_renderer_untouched(mock_resolve, mock_select, mock_session, mock_prepare, mock_post):
    db = MagicMock()
    mock_session.return_value = db

    mock_job = MagicMock()
    mock_job.id = 1
    mock_job.design_title = "Title"
    mock_job.design_subtitle = "Subtitle"
    mock_job.design_caption = "Caption"

    mock_resolve.return_value = MagicMock(
        template_json={"objects": [{"placeholderRole": "title", "titleAccentColor": "#OLD"}]},
        canvas_height=1000,
    )

    call_count = 0

    def fake_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1: return mock_job
        if call_count == 2: return MagicMock(name="Fanpage")
        if call_count == 3: return MagicMock(scraped_title="Art")
        return mock_job

    db.query.return_value.filter.return_value.update.return_value = 1
    db.query.return_value.filter_by.return_value.first.side_effect = fake_first

    mock_select.return_value = ("data:image/png;base64,123", None, "marker")

    tj = {"objects": [{"placeholderRole": "title", "titleAccentColor": "#OLD"}]}
    mock_prepare.return_value = (tj, ["img1"])

    mock_post.return_value.content = b"pngdata"

    with patch('app.services.design_images.single_photo_face_fits', return_value=True), \
         patch('app.services.design_images.focus_points_for', return_value=[]), \
         patch('pathlib.Path.mkdir'), \
         patch('builtins.open', mock_open(read_data=b'123')), \
         patch('pathlib.Path.write_bytes'):
        render_design(1)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    # Mode 2 renderer: no accent mutation
    assert payload["template_json"]["objects"][0]["titleAccentColor"] == "#OLD"


# ── Mode 3 plain path — template_json must NOT be mutated ─────────────────────

@patch('app.tasks.ig_recreate.httpx.post')
@patch('app.services.design_images.prepare_design_images')
@patch('app.tasks.ig_recreate.SessionLocal')
@patch('app.services.design_images.source_news_main')
@patch('app.services.design_images.resolve_template')
def test_live_mode3_payload_unchanged(mock_resolve, mock_source, mock_session, mock_prepare, mock_post):
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

    mock_resolve.return_value = MagicMock(
        template_json={"objects": [{"placeholderRole": "title", "titleAccentColor": "#OLD"}]},
        canvas_height=1000,
    )

    call_count = 0

    def fake_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1: return mock_job
        if call_count == 2: return MagicMock(name="Fanpage", visual_engine="default")
        if call_count == 3: return MagicMock(image_local_paths=["path"])  # post
        if call_count == 4: return MagicMock(
            template_json={"objects": [{"placeholderRole": "title", "titleAccentColor": "#OLD"}]},
            category="news", canvas_height=1000,
        )  # template
        if call_count == 5: return None  # decision
        return None

    db.query.return_value.filter.return_value.update.return_value = 1
    db.query.return_value.filter_by.return_value.first.side_effect = fake_first

    mock_source.return_value = ("data:image/png;base64,123", "path")
    tj = {"objects": [{"placeholderRole": "title", "titleAccentColor": "#OLD"}]}
    mock_prepare.return_value = (tj, ["img1"])
    mock_post.return_value.content = b"pngdata"

    with patch('app.services.design_images.single_photo_face_fits', return_value=True), \
         patch('app.services.design_images.focus_points_for', return_value=[]), \
         patch('pathlib.Path.mkdir'), \
         patch('builtins.open', mock_open(read_data=b'123')), \
         patch('pathlib.Path.write_bytes'):
        render_ig_recreate(1)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    # Plain Mode 3: no accent mutation
    assert payload["template_json"]["objects"][0]["titleAccentColor"] == "#OLD"


# ── Radar path (with decision) — fanpage template, NO colour mutation ──────────

@patch('app.tasks.ig_recreate.httpx.post')
@patch('app.services.design_images.prepare_design_images')
@patch('app.tasks.ig_recreate.SessionLocal')
@patch('app.services.design_images.source_news_main')
@patch('app.services.design_images.resolve_template')
def test_radar_mode3_uses_fanpage_template_no_mutation(
    mock_resolve, mock_source, mock_session, mock_prepare, mock_post
):
    db = MagicMock()
    mock_session.return_value = db

    mock_job = MagicMock()
    mock_job.id = 1
    mock_job.design_title = "Title"
    mock_job.design_subtitle = "Subtitle"
    mock_job.design_caption = "Caption"
    mock_job.design_template_id = 99   # fanpage-assigned template id
    mock_job.post_id = 1
    mock_job.fanpage_id = 1

    fanpage_tpl = {"objects": [{"placeholderRole": "title", "titleAccentColor": "#FANPAGE_COLOR"}]}

    mock_decision = MagicMock()

    call_count = 0

    def fake_first(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1: return mock_job
        if call_count == 2: return MagicMock(name="Fanpage", visual_engine="default")
        if call_count == 3: return MagicMock(image_local_paths=["path"])  # post
        if call_count == 4: return MagicMock(
            template_json=fanpage_tpl,
            category="quote", canvas_height=1000,
        )  # template resolved by design_template_id
        if call_count == 5: return mock_decision  # decision
        return None

    db.query.return_value.filter.return_value.update.return_value = 1
    db.query.return_value.filter_by.return_value.first.side_effect = fake_first

    mock_source.return_value = ("data:image/png;base64,123", "path")
    tj = {"objects": [{"placeholderRole": "title", "titleAccentColor": "#FANPAGE_COLOR"}]}
    mock_prepare.return_value = (tj, ["img1"])
    mock_post.return_value.content = b"pngdata"

    with patch('app.services.design_images.single_photo_face_fits', return_value=True), \
         patch('app.services.design_images.focus_points_for', return_value=[]), \
         patch('pathlib.Path.mkdir'), \
         patch('builtins.open', mock_open(read_data=b'123')), \
         patch('pathlib.Path.write_bytes'):
        render_ig_recreate(1)

    assert mock_post.called
    payload = mock_post.call_args[1]["json"]
    # Radar path: fanpage template colour PRESERVED — no override_template_accents
    assert payload["template_json"]["objects"][0]["titleAccentColor"] == "#FANPAGE_COLOR"


# ── _build_quote_attribution ──────────────────────────────────────────────────

def test_build_quote_attribution_full():
    assert _build_quote_attribution("Nico Rosberg", "Charles Leclerc") == "ROSBERG ON CHARLES LECLERC"

def test_build_quote_attribution_surname_only_speaker():
    assert _build_quote_attribution("SAINZ", "Alonso") == "SAINZ ON ALONSO"

def test_build_quote_attribution_no_secondary():
    assert _build_quote_attribution("Carlos Sainz", "") == "SAINZ"

def test_build_quote_attribution_no_speaker():
    assert _build_quote_attribution("", "Leclerc") == "LECLERC"

def test_build_quote_attribution_both_empty():
    assert _build_quote_attribution("", "") == ""
