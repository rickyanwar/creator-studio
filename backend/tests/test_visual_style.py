import pytest
from app.services.visual_style import choose_accent, choose_treatment, treatment_clause, _ensure_text_contrast
from app.services.visual_engine import build_inset_prompt, build_solo_prompt, build_action_prompt
from app.services.ig_content_classifier import _parse_analysis

class MockFanpage:
    def __init__(self, color):
        self.design_accent_color = color

class MockDriver:
    def __init__(self, color, team_name="Red Bull Racing"):
        self.team_colour = color
        self.team_name = team_name

def test_contrast_helper():
    raw_hex = "#00205B"
    text_hex = _ensure_text_contrast(raw_hex)
    assert text_hex != raw_hex
    assert text_hex.startswith("#")
    
    # Already bright enough
    white_hex = "#FFFFFF"
    assert _ensure_text_contrast(white_hex) == "#FFFFFF"

def test_choose_accent():
    def mock_lookup(name):
        if name == "Verstappen":
            return MockDriver("#0600EF")
        return None
        
    fp = MockFanpage("#FF0000")
    
    # Match F1 driver
    raw, text_safe, name = choose_accent("Verstappen", True, mock_lookup, fp)
    assert raw == "#0600EF"
    assert text_safe != "#0600EF"
    assert "Red Bull Racing blue" in name
    
    # Fallback to fanpage
    raw, text_safe, name = choose_accent("Unknown", True, mock_lookup, fp)
    assert raw == "#FF0000"
    assert "red" in name
    
    # Fallback to default
    raw, text_safe, name = choose_accent("Unknown", True, mock_lookup, MockFanpage(None))
    assert raw == "#C9CED6"
    assert "silver" in name

def test_treatment_precedence():
    assert choose_treatment("news", "positive", "rain") == "rain"
    assert choose_treatment("news", "negative", "rain") == "rain"
    assert choose_treatment("news", "positive", "none") == "celebration"
    assert choose_treatment("news", "negative", "none") == "dramatic"
    assert choose_treatment("news", "neutral", "none") == "clean"
    assert choose_treatment("news", "", "none") == "clean"

def test_prompts_do_not_contain_red_splatter():
    accent = "#0600EF"
    treatment = "celebration"
    
    prompts = [
        build_inset_prompt("hero", [{"mode":"real", "desc":"inset", "path":"p"}], "purpose", accent, "hero.jpg", treatment=treatment)[0],
        build_solo_prompt("hero", "purpose", accent, treatment=treatment),
        build_action_prompt("hero", "purpose", accent, treatment=treatment)
    ]
    
    for p in prompts:
        assert "red lines" not in p.lower()
        assert "dry-brush" not in p.lower()
        assert "subtle red" not in p.lower()
        
        # Verify treatment clause is present
        assert "brighter warmer backdrop" in p.lower()
        assert "no stripes, no lines" in p.lower()
        
def test_classifier_mood_parsing():
    raw_json = '{"type": "news", "text": "test", "mood": "positive"}'
    parsed = _parse_analysis(raw_json, False)
    assert parsed.mood == "positive"
    
    # Missing -> neutral
    raw_json = '{"type": "news", "text": "test"}'
    parsed = _parse_analysis(raw_json, False)
    assert parsed.mood == "neutral"
    
    # Invalid -> neutral
    raw_json = '{"type": "news", "text": "test", "mood": "sad"}'
    parsed = _parse_analysis(raw_json, False)
    assert parsed.mood == "neutral"
