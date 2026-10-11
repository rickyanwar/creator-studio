from unittest.mock import MagicMock, patch
from app.tasks.ig_recreate import _rewrite_news_title

@patch("app.services.ai_caption.generate_caption")
def test_rewrite_news_title_horner(mock_generate):
    fanpage = MagicMock()
    fanpage.name = "F1 News"
    fanpage.caption_language = "English"
    fanpage.mode2_title_max_chars = 80

    text = "£76 MILLION after leaving Red Bull"
    caption = "Christian Horner received £67m after being fired, plus an additional £9m according to Red Bull's 2025 financial report — bringing his total payout to £76m."
    
    # First call missing 76, second call correct
    mock_generate.side_effect = [
        ("BREAKING: Red Bull fires Christian Horner, but he walks away with £67M!", ""),
        ("Christian Horner's total payout reaches **£76M** after leaving Red Bull", "")
    ]
    
    title = _rewrite_news_title(text, caption, fanpage)
    
    assert title == "Christian Horner's total payout reaches **£76M** after leaving Red Bull"
    assert "76" in title
    assert "BREAKING" not in title
    assert mock_generate.call_count == 2
    
    args = mock_generate.call_args_list[0][0][0]
    assert "single NEW fact" in args
    assert "BREAKING" in args
    assert caption in args

@patch("app.services.ai_caption.generate_caption")
def test_rewrite_news_title_fallback(mock_generate):
    fanpage = MagicMock()
    fanpage.name = "F1 News"
    fanpage.caption_language = "English"
    fanpage.mode2_title_max_chars = 80

    text = "**£76 MILLION** after leaving Red Bull"
    caption = "..."
    
    # Both fail to include 76
    mock_generate.side_effect = [
        ("Red Bull fires Christian Horner!", ""),
        ("He walks away with £67M!", "")
    ]
    
    title = _rewrite_news_title(text, caption, fanpage)
    
    # Fallback to cleaned text
    assert title == "£76 MILLION after leaving Red Bull"
