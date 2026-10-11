import json
from unittest.mock import MagicMock, patch
from app.services.ig_content_classifier import _parse_analysis
from app.tasks.ig_recreate import _build_quote_attribution
from app.services.redesign_pipeline import redesign_card

def test_parse_analysis_with_topic():
    raw = json.dumps({
        "type": "quote",
        "text": "I only want to win. But if that means you commit yourself and are driving in P5 or P6, what's the point?",
        "speaker": "Max Verstappen",
        "scenario": "transfer",
        "target_team": "Ferrari",
        "topic": "JOINING FERRARI"
    })
    analysis = _parse_analysis(raw, allow_team_radio=False)
    assert analysis.scenario == "transfer"
    assert analysis.target_team == "Ferrari"
    assert analysis.topic == "JOINING FERRARI"
    
def test_parse_analysis_plain_news():
    raw = json.dumps({
        "type": "news",
        "text": "Driver X wins race",
        "scenario": "none"
    })
    analysis = _parse_analysis(raw, allow_team_radio=False)
    assert analysis.scenario == "none"

def test_build_quote_attribution():
    attr = _build_quote_attribution("Max Verstappen", secondary="SOME OTHER THING", topic="JOINING FERRARI")
    assert attr == "VERSTAPPEN ON JOINING FERRARI"

    attr2 = _build_quote_attribution("Max Verstappen", secondary="SOME OTHER THING", topic="")
    assert attr2 == "VERSTAPPEN ON SOME OTHER THING"

@patch("app.services.redesign_pipeline.plan_photos")
@patch("app.services.redesign_pipeline.generate")
@patch("app.services.redesign_pipeline.check_generated")
@patch("app.services.redesign_pipeline.build_whatif_inset_prompt")
def test_redesign_routing_whatif(mock_build, mock_check, mock_generate, mock_plan):
    mock_plan.return_value = MagicMock(hero=MagicMock(path="hero.jpg"), inset=MagicMock(path="inset.jpg"), extra=[], notes=[], inset_is_distinct=True)
    mock_check.return_value = MagicMock(passed=True)
    mock_generate.return_value = b"fake"
    mock_build.return_value = ("what-if prompt", ["hero.jpg"])
    
    analysis = _parse_analysis(json.dumps({
        "type": "quote",
        "speaker": "Max Verstappen",
        "main_subject": "Max Verstappen",
        "scenario": "transfer",
        "target_team": "Ferrari",
        "topic": "JOINING FERRARI",
        "mood": "neutral"
    }), allow_team_radio=False)

    db = MagicMock()
    post = MagicMock(image_local_paths=["test.jpg"])
    fanpage = MagicMock(radar_niches=["F1"], id=1)
    
    with patch("builtins.open"), patch("app.services.redesign_pipeline.is_f1_niche", return_value=True), \
         patch("app.services.visual_style.choose_accent", return_value=("accent", "accent", "accent")), \
         patch("app.services.visual_style.choose_treatment", return_value="quote_neutral"), \
         patch("app.services.redesign_pipeline.upscale_image_bytes", return_value=b"fake"), \
         patch("os.makedirs"), patch("hashlib.md5") as mock_md5:
         
        # Make the query return a mock driver
        db.query.return_value.filter.return_value.first.return_value = MagicMock(team_colour="red", logo_path="ferrari.png")
        
        redesign_card(db, post=post, fanpage=fanpage, job=None, pre_analysis=analysis)
        
        mock_build.assert_called_once_with(
            hero_desc="Max Verstappen",
            target_team="Ferrari",
            target_team_color="red",
            target_team_logo_desc="Ferrari logo",
            accent="accent",
            hero_path="hero.jpg",
            alt_path=None,
            kit_path=None,
            sport="Formula 1",
            treatment="quote_neutral"
        )
