from app.services.visual_qa import check_generated
from unittest.mock import patch, MagicMock

@patch("app.services.visual_qa._vision_chat")
@patch("app.services.visual_qa._vision_datauri")
@patch("builtins.open")
def test_whatif_qa_exemption(mock_open, mock_datauri, mock_chat):
    mock_chat.return_value = '{"identity_hero": true, "identity_inset": true, "invented_or_garbled_logos": [], "missing_real_logos": [], "text_on_image": [], "source_branding_visible": false, "ai_look": 1, "bottom_area_empty": true}'
    mock_datauri.return_value = "data:image/jpeg;base64,..."
    
    mock_open.return_value.__enter__.return_value.read.return_value = b"bytes"
    
    res = check_generated(b"gen", layout="inset", hero_ref_path="/a", inset_ref_paths=("/b",), whatif_target_team="Ferrari")
    
    assert res.passed
    # Check that prompt has the exemption
    args, kwargs = mock_chat.call_args
    prompt_text = args[0][-1]["text"]
    assert "what-if" in prompt_text.lower()
    assert "Ferrari" in prompt_text

