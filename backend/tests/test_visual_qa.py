import pytest
from unittest.mock import patch, mock_open
from app.services.visual_qa import (
    normalise_text, radio_text_matches, parse_qa_json, evaluate, check_generated, QAResult
)

def test_normalise_text():
    assert normalise_text("Hello ‘World’!") == "HELLO 'WORLD'!"
    assert normalise_text("“Quotes” … and spaces  ") == '"QUOTES" ... AND SPACES'
    assert normalise_text("No 1!?.,'") == "NO 1!?.,'"
    assert normalise_text("symbols #@$% removed") == "SYMBOLS REMOVED"

def test_radio_text_matches():
    ocr = "HAMILTON RADIO 44 'Bono, my tyres are gone...'"
    header = ("Hamilton", "RADIO", "44")
    lines = ("'Bono, my tyres are gone...'",)
    
    # Matches exactly
    assert radio_text_matches(ocr, header, lines) is None
    
    # Missing header token
    assert radio_text_matches(ocr, ("Verstappen", "RADIO", "44"), lines) == "Verstappen"
    
    # Missing line
    assert radio_text_matches(ocr, header, ("'I am fast'",)) == "'I am fast'"
    
    # Normalised matches
    ocr2 = "hamilton radio 44 ‘Bono, my tyres are gone…’"
    assert radio_text_matches(ocr2, header, lines) is None

def test_parse_qa_json():
    # Markdown
    raw = """```json\n{"ai_look": 2}\n```"""
    assert parse_qa_json(raw) == {"ai_look": 2}
    
    # Fenced but no 'json'
    raw2 = """```\n{"ai_look": 3}\n```"""
    assert parse_qa_json(raw2) == {"ai_look": 3}
    
    # Raw JSON
    raw3 = '{"ai_look": 1}'
    assert parse_qa_json(raw3) == {"ai_look": 1}

def test_evaluate():
    # Pass case for non-radio
    qa_pass = {
        "identity_hero": True,
        "identity_inset": True,
        "invented_or_garbled_logos": [],
        "missing_real_logos": [],
        "text_on_image": [],
        "source_branding_visible": False,
        "ai_look": 2,
        "bottom_area_empty": True
    }
    assert evaluate(qa_pass, "portrait", None) == (True, ())
    
    # Fail identity
    qa_fail_id = {**qa_pass, "identity_hero": False}
    assert evaluate(qa_fail_id, "portrait", None) == (False, ("identity_changed",))
    
    # Fail logo invented (Now a warning, should pass)
    qa_fail_logo_inv = {**qa_pass, "invented_or_garbled_logos": ["weird logo"]}
    assert evaluate(qa_fail_logo_inv, "portrait", None) == (True, ())
        
    # Fail logo missing (Now a warning, should pass)
    qa_fail_logo_miss = {**qa_pass, "missing_real_logos": ["red bull", "honda"]}
    assert evaluate(qa_fail_logo_miss, "portrait", None) == (True, ())
    
# Fail logo missing (must be >1)
    qa_fail_logo_miss_1 = {**qa_pass, "missing_real_logos": ["one"]}
    assert evaluate(qa_fail_logo_miss_1, "portrait", None) == (True, ())
    qa_fail_logo_miss_2 = {**qa_pass, "missing_real_logos": ["one", "two"]}
    assert evaluate(qa_fail_logo_miss_2, "portrait", None) == (True, ())
    
    # Fail text on image (non-radio)
    qa_fail_text = {**qa_pass, "text_on_image": ["hello"]}
    assert evaluate(qa_fail_text, "portrait", None) == (False, ("text_present",))
    
    # Text on image allowed for radio
    assert evaluate(qa_fail_text, "team_radio", None) == (True, ())
    
    # Fail source branding
    qa_fail_brand = {**qa_pass, "source_branding_visible": True}
    assert evaluate(qa_fail_brand, "portrait", None) == (False, ("source_branding",))
    
    # Fail AI look
    qa_fail_ai = {**qa_pass, "ai_look": 4}
    assert evaluate(qa_fail_ai, "portrait", None) == (False, ("looks_ai",))
    
    # Fail bottom not empty (non-radio)
    qa_fail_bottom = {**qa_pass, "bottom_area_empty": False}
    assert evaluate(qa_fail_bottom, "portrait", None) == (False, ("bottom_not_empty",))
    assert evaluate(qa_fail_bottom, "team_radio", None) == (True, ())
    
    # Radio text mismatch
    assert evaluate(qa_pass, "team_radio", "'I am fast'") == (False, ("radio_text_mismatch:'I am fast'",))

@patch("app.services.visual_qa._vision_chat")
@patch("app.services.visual_qa._vision_datauri", return_value="data:image/png;base64,xxx")
@patch("builtins.open", new_callable=mock_open, read_data=b"img")
def test_check_generated_pass(mock_file, mock_uri, mock_chat):
    mock_chat.return_value = '{"identity_hero": true}'
    
    res = check_generated(
        b"gen", 
        layout="portrait", 
        hero_ref_path="hero.jpg",
        source_branding=("@f1",)
    )
    
    assert res.passed is True
    assert res.problems == ()
    assert mock_chat.call_count == 1
    # Check that it opens hero file
    mock_file.assert_called_with("hero.jpg", "rb")

@patch("app.services.visual_qa._vision_chat")
@patch("app.services.visual_qa._vision_datauri", return_value="data:image/png;base64,xxx")
@patch("builtins.open", new_callable=mock_open, read_data=b"img")
def test_check_generated_radio(mock_file, mock_uri, mock_chat):
    mock_chat.side_effect = [
        '{"identity_hero": true}',  # qa json
        "HAMILTON RADIO 44 'Bono'"  # ocr
    ]
    
    res = check_generated(
        b"gen", 
        layout="team_radio", 
        hero_ref_path="hero.jpg",
        expected_header=("HAMILTON",),
        expected_lines=("'Bono'",)
    )
    
    assert res.passed is True
    assert res.ocr_text == "HAMILTON RADIO 44 'Bono'"
    assert mock_chat.call_count == 2

@patch("app.services.visual_qa._vision_chat")
@patch("app.services.visual_qa._vision_datauri", return_value="data:image/png;base64,xxx")
@patch("builtins.open", new_callable=mock_open, read_data=b"img")
def test_check_generated_unavailable(mock_file, mock_uri, mock_chat):
    mock_chat.side_effect = Exception("network error")
    
    res = check_generated(b"gen", layout="portrait", hero_ref_path="hero.jpg")
    
    assert res.passed is False
    assert res.problems == ("qa_unavailable",)
