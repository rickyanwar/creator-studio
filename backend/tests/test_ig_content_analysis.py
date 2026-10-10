import json
from unittest.mock import patch
from app.services.ig_content_classifier import (
    is_f1_niche,
    _parse_analysis,
    analyze_ig_post,
    PostAnalysis,
)

def test_is_f1_niche():
    assert is_f1_niche(["F1"]) is True
    assert is_f1_niche([" formula 1 "]) is True
    assert is_f1_niche(["formula1", "general"]) is True
    assert is_f1_niche(["MotoGP"]) is False
    assert is_f1_niche([]) is False
    assert is_f1_niche(["general", "ufc"]) is False

def test_parse_analysis_news():
    raw = '```json\n{"type": "news", "text": "Alonso moves to Aston Martin", "main_subject": "Alonso", "unique_moment": true}\n```'
    parsed = _parse_analysis(raw, allow_team_radio=True)
    assert parsed.type == "news"
    assert parsed.text == "Alonso moves to Aston Martin"
    assert parsed.main_subject == "Alonso"
    assert parsed.unique_moment is True
    assert parsed.weather == "none"

def test_parse_analysis_quote():
    raw = '{"type": "quote", "text": "I am happy.", "speaker": "Max"}'
    parsed = _parse_analysis(raw, allow_team_radio=True)
    assert parsed.type == "quote"
    assert parsed.text == "I am happy."
    assert parsed.speaker == "Max"
    assert parsed.radio_lines == ()

def test_parse_analysis_team_radio_allowed():
    raw = '{"type": "team_radio", "main_subject": "Max", "radio_lines": [["engineer", "Box now"], ["driver", "Copy"], ["engineer", "Wait"]]}'
    parsed = _parse_analysis(raw, allow_team_radio=True)
    assert parsed.type == "team_radio"
    assert parsed.radio_lines == (("engineer", "Box now"), ("driver", "Copy"), ("engineer", "Wait"))
    assert parsed.main_subject == "Max"

def test_parse_analysis_team_radio_not_allowed():
    raw = '{"type": "team_radio", "main_subject": "Max", "radio_lines": [["engineer", "Box now"], ["driver", "Copy"]]}'
    parsed = _parse_analysis(raw, allow_team_radio=False)
    assert parsed.type == "quote"
    assert parsed.text == "Box now / Copy"
    assert parsed.speaker == "Max"

def test_parse_analysis_press_quote_mentioning_radio():
    # If the parser sees "quote" but mentions radio, it shouldn't coerce it just because allow_team_radio=False
    raw = '{"type": "quote", "text": "I said on the radio that I was unhappy.", "speaker": "Lewis"}'
    parsed = _parse_analysis(raw, allow_team_radio=False)
    assert parsed.type == "quote"
    assert parsed.text == "I said on the radio that I was unhappy."
    assert parsed.speaker == "Lewis"

def test_parse_analysis_fenced_and_prose():
    raw = 'Here is the result:\n```\n{"type": "other", "weather": "rainy"}\n```\nDone.'
    parsed = _parse_analysis(raw, allow_team_radio=True)
    assert parsed.type == "other"
    assert parsed.weather == "none"  # only "rain" is kept, otherwise "none"

def test_parse_analysis_missing_fields():
    raw = '{"type": "news"}'
    parsed = _parse_analysis(raw, allow_team_radio=True)
    assert parsed.type == "news"
    assert parsed.text == ""
    assert parsed.speaker == ""
    assert parsed.main_subject == ""
    assert parsed.secondary_kind == "none"
    assert parsed.weather == "none"
    assert parsed.people == ()
    assert parsed.unique_moment is False
    assert parsed.radio_lines == ()

def test_parse_analysis_bad_role_and_weather():
    raw = '{"type": "team_radio", "radio_lines": [["mechanic", "fix it"]], "weather": "sunny"}'
    parsed = _parse_analysis(raw, allow_team_radio=True)
    assert parsed.type == "team_radio"
    assert parsed.radio_lines == (("driver", "fix it"),) # mechanic -> driver
    assert parsed.weather == "none" # sunny -> none

@patch('app.services.design_images._vision_chat')
@patch('app.services.nine_router.get_nine_router_config')
def test_analyze_ig_post_end_to_end(mock_get_config, mock_vision_chat):
    class MockConfig:
        base_url = "http://mock"
    mock_get_config.return_value = MockConfig()
    
    mock_vision_chat.return_value = '{"type": "news", "text": "Breaking news", "main_subject": "F1"}'
    
    # Needs dummy image bytes
    result = analyze_ig_post(b"dummy_image", caption="Test caption", niche="F1", allow_team_radio=True)
    
    assert isinstance(result, PostAnalysis)
    assert result.type == "news"
    assert result.text == "Breaking news"
    assert result.main_subject == "F1"
    
    # Check vision chat was called
    assert mock_vision_chat.called
    args = mock_vision_chat.call_args[0][0]
    assert len(args) == 2
    assert "Test caption" in args[0]["text"]
