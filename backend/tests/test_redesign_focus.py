import pytest
from unittest.mock import patch, MagicMock
from app.services.design_images import focus_points_for
from app.services.redesign_pipeline import redesign_card
from app.services.visual_engine import GenerationResult
from app.services.identity_check import IdentityResult
from app.models.publish_jobs import PublishJob
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.design_templates import DesignTemplate


def test_focus_points_for_keep_top():
    srcs = ["data:image/png;base64,abc", "data:image/png;base64,def"]
    fps = focus_points_for(srcs, keep_top=True)
    assert fps == [[0.5, 0.0], [0.5, 0.0]]


def test_focus_points_for_default_keep_top_false():
    srcs = ["data:image/png;base64,invalid"]
    fps = focus_points_for(srcs)
    # Default without keep_top falls back to [0.5, 0.42] when decoding fails
    assert fps == [[0.5, 0.42]]


@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.generate_ex")
@patch("app.services.redesign_pipeline.check_identity")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes")
@patch("app.services.design_images.focus_points_for")
@patch("httpx.post")
@patch("builtins.open")
@patch("os.makedirs")
def test_redesign_card_uses_keep_top(
    mock_makedirs,
    mock_open,
    mock_httpx_post,
    mock_focus_points,
    mock_upscale,
    mock_check,
    mock_identity,
    mock_generate,
    mock_plan,
    mock_layout,
    mock_analyze,
):
    post = Post(image_local_paths=["/source.jpg"])
    fanpage = TargetFanpage(radar_niches=["F1"], name="F1Page")
    tpl = DesignTemplate(
        id=7,
        name="News Highlight",
        canvas_width=1080,
        canvas_height=1350,
        template_json={"objects": [{"placeholderRole": "image", "width": 1080, "height": 1350}]},
    )
    job = PublishJob(
        id=13019,
        design_template_id=7,
        design_title="Test Title",
    )
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = tpl

    mock_analyze.return_value = MagicMock(type="news", main_subject="Leclerc", moment_summary="Moment", mood="positive")
    mock_layout.return_value = MagicMock(layout="solo")
    mock_plan_res = MagicMock()
    mock_plan_res.hero = MagicMock(path="/hero.jpg")
    mock_plan_res.inset = None
    mock_plan_res.extra = []
    mock_plan_res.notes = []
    mock_plan.return_value = mock_plan_res

    mock_generate.return_value = GenerationResult(b"fake_png", "gptcli", {}, None)
    mock_identity.return_value = IdentityResult(passed=True, sface=0.9, gemini="ok", reason="matched")
    mock_check.return_value = MagicMock(passed=True, problems=[])
    mock_focus_points.return_value = [[0.5, 0.0]]
    mock_resp = MagicMock()
    mock_resp.content = b"rendered_png"
    mock_resp.raise_for_status = MagicMock()
    mock_httpx_post.return_value = mock_resp
    mock_upscale.return_value = b"upscaled_png"

    res = redesign_card(db, post=post, fanpage=fanpage, job=job)
    assert res.ok is True
    mock_focus_points.assert_called_once()
    _, kwargs = mock_focus_points.call_args
    assert kwargs.get("keep_top") is True
