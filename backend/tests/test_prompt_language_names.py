from types import SimpleNamespace
from unittest.mock import patch
import pytest

from app.services.ai_caption import build_caption_prompt
from app.services.facebook_photo_source import _localize_quote
from app.services.news_copywriter import (
    _discussion_caption_block,
    build_discussion_news_prompt,
    build_news_copy_prompt,
)


def _make_fanpage(lang: str = "fr", **kwargs):
    defaults = {
        "id": 1,
        "name": "GP Weekend",
        "caption_language": lang,
        "mode2_title_max_chars": 80,
        "mode2_source_attribution": True,
        "quote_shorten_enabled": False,
        "discussion_label_mode": "both",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_article(**kwargs):
    defaults = {
        "id": 10,
        "scraped_title": "Ferrari fastest in practice",
        "scraped_content": "Charles Leclerc sets the fastest lap time.",
        "news_source": SimpleNamespace(name="L'Equipe"),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_news_copy_prompt_french():
    fp = _make_fanpage("fr")
    art = _make_article()
    prompt = build_news_copy_prompt(fp, art)

    assert "French" in prompt
    assert "ALWAYS" in prompt
    assert (
        'The "title" and "subtitle" are printed on the image: both MUST be in French. Never leave them in the source language.'
        in prompt
    )
    assert "Translate to fr" not in prompt
    assert "Language: fr" not in prompt


def test_news_copy_prompt_english_no_regression():
    fp = _make_fanpage("en")
    art = _make_article()
    prompt = build_news_copy_prompt(fp, art)

    assert "English" in prompt
    assert "ALWAYS" in prompt
    assert (
        'The "title" and "subtitle" are printed on the image: both MUST be in English. Never leave them in the source language.'
        in prompt
    )
    assert "Translate to en" not in prompt
    assert "Language: en" not in prompt


def test_discussion_prompts_contain_language_name():
    fp = _make_fanpage("fr")
    art = _make_article()

    cap_block = _discussion_caption_block(fp, "L'Equipe")
    assert "- Language: French" in cap_block

    disc_news_prompt = build_discussion_news_prompt(fp, art)
    assert "Language: French" in disc_news_prompt


def test_ai_caption_prompt_contains_language_name():
    fp = _make_fanpage("fr")
    prompt = build_caption_prompt(fp, "source_user", "orig text")
    assert "- Language: French" in prompt


def test_facebook_photo_source_mode6_contains_language_name():
    fp = _make_fanpage("fr")
    with patch("app.services.ai_caption.generate_caption", return_value=("Quote", "gemini")) as mock_ai:
        _localize_quote("Original quote", fp)
        prompt = mock_ai.call_args[0][0]
        assert "French" in prompt
        assert "Translate this quote to French" in prompt
