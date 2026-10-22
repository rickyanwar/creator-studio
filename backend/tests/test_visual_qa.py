import pytest
from unittest.mock import patch, mock_open
from app.services.visual_qa import (
    normalise_text, radio_text_matches, parse_qa_json, evaluate, check_generated, QAResult
)


@pytest.fixture(autouse=True)
def no_sleep_on_retry(monkeypatch):
    monkeypatch.setattr("app.services.vision_retry.time.sleep", lambda s: None)


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
    raw = """```json\n{"identity_hero": true}\n```"""
    assert parse_qa_json(raw) == {"identity_hero": True}
    
    # Fenced but no 'json'
    raw2 = """```\n{"identity_hero": true}\n```"""
    assert parse_qa_json(raw2) == {"identity_hero": True}
    
    # Raw JSON
    raw3 = '{"identity_hero": true}'
    assert parse_qa_json(raw3) == {"identity_hero": True}


def test_evaluate():
    # Pass case for non-radio
    qa_pass = {
        "identity_hero": True,
        "identity_inset": True,
        "source_branding_visible": False,
    }
    assert evaluate(qa_pass, "portrait", None) == (True, ())
    
    # Fail identity
    qa_fail_id = {**qa_pass, "identity_hero": False}
    assert evaluate(qa_fail_id, "portrait", None) == (False, ("identity_changed",))
    
    # Fail source branding
    qa_fail_brand = {**qa_pass, "source_branding_visible": True}
    assert evaluate(qa_fail_brand, "portrait", None) == (False, ("source_branding",))
    
    # Radio text mismatch
    assert evaluate(qa_pass, "team_radio", "'I am fast'") == (False, ("radio_text_mismatch:'I am fast'",))


def test_evaluate_safety_vs_non_blocking_notes():
    # plain_whatif_suit is a non-blocking note problem
    qa_whatif = {
        "identity_hero": True,
        "identity_inset": True,
        "plain_whatif_suit": True,
        "source_branding_visible": False,
    }
    passed, problems = evaluate(qa_whatif, "portrait", None, whatif_target_team="Ferrari")
    assert passed is True
    assert set(problems) == {"note:plain_whatif_suit"}

    # identity_hero False -> passed False
    qa_id_fail = {**qa_whatif, "identity_hero": False}
    passed, problems = evaluate(qa_id_fail, "portrait", None, whatif_target_team="Ferrari")
    assert passed is False
    assert "identity_changed" in problems

    # source_branding -> passed False
    qa_branding_fail = {**qa_whatif, "source_branding_visible": True}
    passed, problems = evaluate(qa_branding_fail, "portrait", None, whatif_target_team="Ferrari")
    assert passed is False
    assert "source_branding" in problems

    # radio mismatch -> passed False
    qa_pass = {
        "identity_hero": True,
        "identity_inset": True,
        "source_branding_visible": False,
    }
    passed, problems = evaluate(qa_pass, "team_radio", "MISSING_TEXT")
    assert passed is False
    assert "radio_text_mismatch:MISSING_TEXT" in problems


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
