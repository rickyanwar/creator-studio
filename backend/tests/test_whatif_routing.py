from unittest.mock import MagicMock, patch
from app.services.ig_content_classifier import analyze_ig_post, _parse_analysis
from app.services.layout_chooser import choose_layout

def test_classifier_parsing_whatif():
    raw_json = """
    {
        "type": "quote",
        "text": "What if Max joined Ferrari?",
        "speaker": "Christian Horner",
        "main_subject": "Max Verstappen",
        "secondary_kind": "none",
        "secondary": "",
        "inset_context": "",
        "inset_query": "",
        "moment_summary": "",
        "radio_lines": [],
        "weather": "none",
        "mood": "neutral",
        "people": ["Max Verstappen", "Christian Horner"],
        "unique_moment": false,
        "scenario": "hypothetical",
        "target_team": "Ferrari"
    }
    """
    analysis = _parse_analysis(raw_json, allow_team_radio=False)
    assert analysis.scenario == "hypothetical"
    assert analysis.target_team == "Ferrari"
    
def test_layout_chooser_whatif():
    raw_json = """
    {
        "type": "quote",
        "text": "What if Max joined Ferrari?",
        "speaker": "Christian Horner",
        "main_subject": "Max Verstappen",
        "secondary_kind": "none",
        "secondary": "",
        "inset_context": "",
        "inset_query": "",
        "moment_summary": "",
        "radio_lines": [],
        "weather": "none",
        "mood": "neutral",
        "people": ["Max Verstappen", "Christian Horner"],
        "unique_moment": false,
        "scenario": "hypothetical",
        "target_team": "Ferrari"
    }
    """
    analysis = _parse_analysis(raw_json, allow_team_radio=False)
    
    # Even if has_inset_photo is False (since we didn't find one for the quote),
    # what-if routing should force 'inset'.
    choice = choose_layout(analysis, is_f1=True, hero_label="face", has_inset_photo=False)
    assert choice.layout == "inset"
    assert choice.reason == "Transfer scenario what-if inset"

