import os
import pytest
from unittest.mock import patch, MagicMock
from app.services.redesign_pipeline import redesign_card
from app.models.publish_jobs import PublishJob
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.models.radar import RadarStoryDecision

@pytest.fixture
def dummy_deps():
    post = Post(image_local_paths=["/source.jpg"])
    fanpage = TargetFanpage(radar_niches=["F1"])
    return post, fanpage

@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes")
@patch("builtins.open")
@patch("os.makedirs")
def test_redesign_team_radio(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_generate, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    
    analysis = MagicMock(type="team_radio", main_subject="Max", radio_lines=[("driver", "yes")])
    mock_analyze.return_value = analysis
    
    mock_layout.return_value = MagicMock(layout="team_radio")
    
    mock_plan_res = MagicMock()
    mock_plan_res.hero = MagicMock(path="/hero.jpg", gallery_id=1)
    mock_plan_res.inset = None
    mock_plan_res.notes = []
    mock_car = MagicMock(path="/car.jpg"); mock_car.gallery_id = 2; mock_plan_res.extra = [mock_car]
    mock_plan.return_value = mock_plan_res
    
    driver = MagicMock(name="Max Verstappen", number="1", colour_hex="#000", team="RBR")
    mock_driver.return_value = driver
    
    mock_generate.return_value = b"gen"
    mock_check.return_value = MagicMock(passed=True, problems=[])
    mock_upscale.return_value = b"upscaled"
    
    decision = RadarStoryDecision(id=1)
    
    # Team radio skips template rendering
    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=decision)
    
    assert res.ok is True
    assert res.layout == "team_radio"
    assert res.final_text_rendered_by == "ai"
    assert mock_generate.call_count == 1
    
    req = mock_generate.call_args[0][1]  # generate(db, req, ...)
    assert len(req.refs) == 4
    assert req.refs[0] == "/hero.jpg"
    assert req.refs[1] == "/car.jpg"
    assert req.refs[2].endswith("style_hadjar.png")
    assert req.refs[3].endswith("style_radio_format.png")
    assert os.path.isfile(req.refs[2]) and os.path.isfile(req.refs[3])
    
    assert decision.used_photo_key == "/hero.jpg|/car.jpg"

@patch("app.services.redesign_pipeline.analyze_ig_post")
@patch("app.services.redesign_pipeline.choose_layout")
@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.get_driver")
@patch("app.services.redesign_pipeline.generate")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.upscale_image_bytes")
@patch("builtins.open")
@patch("os.makedirs")
def test_redesign_qa_fail_retry(
    mock_makedirs, mock_open, mock_upscale, mock_check, mock_generate, mock_driver, mock_plan, mock_layout, mock_analyze, dummy_deps
):
    post, fanpage = dummy_deps
    
    analysis = MagicMock(type="team_radio", main_subject="Max", radio_lines=[])
    mock_analyze.return_value = analysis
    
    mock_layout.return_value = MagicMock(layout="team_radio")
    
    mock_plan_res = MagicMock()
    mock_plan_res.hero = MagicMock(path="/hero.jpg", gallery_id=1)
    mock_plan_res.inset = None
    mock_plan_res.notes = []
    mock_car = MagicMock(path="/car.jpg"); mock_car.gallery_id = 2; mock_plan_res.extra = [mock_car]
    mock_plan.return_value = mock_plan_res
    
    driver = MagicMock(name="Max", number="1", colour_hex="#000", team="RBR")
    mock_driver.return_value = driver
    
    mock_generate.return_value = b"gen"
    mock_upscale.return_value = b"upscaled"
    
    # QA fails first time, succeeds second time
    mock_check.side_effect = [
        MagicMock(passed=False, problems=["Bad"]),
        MagicMock(passed=True, problems=[])
    ]
    
    res = redesign_card(MagicMock(), post=post, fanpage=fanpage, job=None, decision=None)
    
    assert res.ok is True
    assert mock_generate.call_count == 2
    assert mock_check.call_count == 2
