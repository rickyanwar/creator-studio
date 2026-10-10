import pytest
from unittest.mock import patch
from app.services.ig_content_classifier import analyze_ig_post, is_f1_niche

def test_is_f1_niche():
    assert is_f1_niche(["F1"]) is True
    assert is_f1_niche(["Formula 1"]) is True
    assert is_f1_niche(["UFC"]) is False

@patch("app.services.design_images._vision_chat")
@patch("app.services.nine_router.get_nine_router_config")
def test_analyze_ig_post_team_radio_misjudge(mock_config, mock_vision):
    class DummyCfg:
        base_url = "http://dummy"
    mock_config.return_value = DummyCfg()
    
    # Mocking the vision call to return the stubborn 'quote' output
    # because the model sees a microphone and thinks it's an interview
    mock_vision.return_value = """
    {
      "type": "quote",
      "text": "I THINK HE COMPLAINED ON THE RADIO",
      "speaker": "Carlos Sainz",
      "main_subject": "Carlos Sainz",
      "secondary_kind": "person",
      "secondary": "Fernando Alonso",
      "moment_summary": "Sainz comments",
      "radio_lines": [],
      "weather": "none",
      "people": ["Carlos Sainz", "Fernando Alonso"],
      "unique_moment": false
    }
    """
    
    res = analyze_ig_post(b"dummy", "", niche="F1", allow_team_radio=True)
    # The prompt was tightened but the model still outputs quote.
    assert res.type == "quote"


@patch("app.services.design_images._vision_chat")
@patch("app.services.nine_router.get_nine_router_config")
def test_analyze_ig_post_team_radio_allowed(mock_config, mock_vision):
    class DummyCfg:
        base_url = "http://dummy"
    mock_config.return_value = DummyCfg()
    
    mock_vision.return_value = """
    {
      "type": "team_radio",
      "text": "I THINK HE COMPLAINED ON THE RADIO",
      "speaker": "Carlos Sainz",
      "main_subject": "Carlos Sainz"
    }
    """
    
    res = analyze_ig_post(b"dummy", "", niche="F1", allow_team_radio=True)
    assert res.type == "team_radio"

@patch("app.services.design_images._vision_chat")
@patch("app.services.nine_router.get_nine_router_config")
def test_analyze_ig_post_team_radio_disallowed(mock_config, mock_vision):
    class DummyCfg:
        base_url = "http://dummy"
    mock_config.return_value = DummyCfg()
    
    mock_vision.return_value = """
    {
      "type": "team_radio",
      "text": "I THINK HE COMPLAINED ON THE RADIO",
      "speaker": "Carlos Sainz",
      "main_subject": "Carlos Sainz"
    }
    """
    
    res = analyze_ig_post(b"dummy", "", niche="F1", allow_team_radio=False)
    # The parser should downgrade it to 'quote'
    assert res.type == "quote"
