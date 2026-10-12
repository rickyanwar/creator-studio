import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.services.news_copywriter import (
    template_title_fit_chars,
    build_news_copy_prompt,
    generate_news_copy,
    NewsCopy,
)


def _make_fanpage(**kwargs):
    defaults = {
        "id": 1,
        "name": "MotoGP Hub",
        "mode2_title_max_chars": 80,
        "mode2_source_attribution": True,
        "mode2_caption_language": "en",
        "mode2_caption_tone": "informative",
        "mode2_caption_max_length": 500,
        "mode2_caption_hashtag_count": 5,
        "mode2_caption_cta_text": "",
        "mode2_caption_custom_prompt": "",
        "quote_shorten_enabled": False,
        "default_news_template_id": None,
        "default_quote_template_id": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _make_article(**kwargs):
    defaults = {
        "id": 10,
        "scraped_title": "Marc Marquez dominates at Aragon to secure victory",
        "scraped_content": "Marc Marquez won the race with an outstanding performance.",
        "news_source": SimpleNamespace(name="Crash.net", caption_language=None, caption_tone=None, caption_max_length=None, caption_hashtag_count=None, caption_cta_text=None, caption_custom_prompt=None),
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── Tests for template_title_fit_chars helper ───────────────────────────────────

def test_template_title_fit_chars_returns_val_when_present():
    db = MagicMock()
    fp = _make_fanpage()
    mock_tpl = SimpleNamespace(
        template_json={
            "objects": [
                {"placeholderRole": "image"},
                {"placeholderRole": "title", "titleFitChars": 64},
            ]
        }
    )
    with patch("app.services.design_images.resolve_template", return_value=mock_tpl):
        val = template_title_fit_chars(db, fp, category="news")
        assert val == 64


def test_template_title_fit_chars_returns_none_when_absent():
    db = MagicMock()
    fp = _make_fanpage()
    mock_tpl = SimpleNamespace(
        template_json={
            "objects": [
                {"placeholderRole": "title"},
            ]
        }
    )
    with patch("app.services.design_images.resolve_template", return_value=mock_tpl):
        assert template_title_fit_chars(db, fp, category="news") is None


def test_template_title_fit_chars_returns_none_on_non_positive_int():
    db = MagicMock()
    fp = _make_fanpage()
    mock_tpl = SimpleNamespace(
        template_json={
            "objects": [
                {"placeholderRole": "title", "titleFitChars": 0},
            ]
        }
    )
    with patch("app.services.design_images.resolve_template", return_value=mock_tpl):
        assert template_title_fit_chars(db, fp, category="news") is None


def test_template_title_fit_chars_returns_none_on_broken_json_or_error():
    db = MagicMock()
    fp = _make_fanpage()
    mock_tpl = SimpleNamespace(template_json="invalid json string not dict")
    with patch("app.services.design_images.resolve_template", return_value=mock_tpl):
        assert template_title_fit_chars(db, fp, category="news") is None

    with patch("app.services.design_images.resolve_template", side_effect=Exception("DB error")):
        assert template_title_fit_chars(db, fp, category="news") is None


# ── Tests for build_news_copy_prompt ──────────────────────────────────────────

def test_build_news_copy_prompt_fit_chars_none_identical_to_legacy():
    fp = _make_fanpage()
    art = _make_article()
    prompt = build_news_copy_prompt(fp, art, fit_chars=None)
    assert "Keep roughly the SAME LENGTH as the source TITLE" in prompt
    assert "do NOT shorten it or compress it into a vague topic label" in prompt
    assert "The headline is printed in big letters in a fixed space: it MUST be at most" not in prompt
    assert "Do NOT start with 'BREAKING:' / 'OFFICIAL:' / 'CONFIRMED:'" not in prompt


def test_build_news_copy_prompt_fit_chars_set():
    fp = _make_fanpage()
    art = _make_article()
    prompt = build_news_copy_prompt(fp, art, fit_chars=64)
    assert "it MUST be at most 64 characters and should USE most of that space: aim for 48 to 64 characters (the ** markers do not count)." in prompt
    assert "Make it MORE engaging, not flatter: lead with the name or the hook" in prompt
    assert "Always keep the key context a fan needs: the event/place (e.g. Mandalika, Indonesian GP), the result or the concrete injury/fact. Cut filler, never context." in prompt
    assert "Do NOT start with 'BREAKING:' / 'OFFICIAL:' / 'CONFIRMED:'" in prompt
    assert "Bagnaia crashes out in Indonesia: **No feeling, no grip**" in prompt
    assert "do NOT shorten it" not in prompt


def test_build_news_copy_prompt_quote_shorten_toggle_off():
    fp = _make_fanpage(quote_shorten_enabled=False)
    art = _make_article()
    prompt = build_news_copy_prompt(fp, art)
    assert "Use the speaker's EXACT words from the article, word for word" in prompt
    assert "Do NOT shorten, summarise, merge sentences or drop clauses" in prompt
    assert "Maximum 140 characters." not in prompt


def test_build_news_copy_prompt_quote_shorten_toggle_on():
    fp = _make_fanpage(quote_shorten_enabled=True)
    art = _make_article()
    prompt = build_news_copy_prompt(fp, art, quote_fit_chars=90)
    assert "You may shorten the quote to at most 90 characters" in prompt
    assert "use '…' where words were cut" in prompt


# ── Tests for generate_news_copy retry behavior ───────────────────────────────

def test_generate_news_copy_within_budget_no_retry():
    fp = _make_fanpage()
    art = _make_article()
    first_resp = {
        "type": "news",
        "is_breaking": False,
        "title": "Marquez wins **at Aragon**",
        "subtitle": "Strong performance",
        "caption": "Full caption here",
    }
    with patch("app.services.news_copywriter.template_title_fit_chars", side_effect=lambda db, fp, cat: 64 if cat == "news" else None), \
         patch("app.services.news_copywriter._generate_with_fallback", return_value=((first_resp["type"], first_resp["is_breaking"], first_resp["title"], first_resp["subtitle"], first_resp["caption"]), "gemini")) as mock_gen:
        res = generate_news_copy(fp, art)
        assert mock_gen.call_count == 1
        assert res.title == "Marquez wins **at Aragon**"


def test_generate_news_copy_over_budget_news_triggers_one_retry():
    fp = _make_fanpage()
    art = _make_article()
    # 70 chars without ** markers, budget is 50
    long_title = "Marc Marquez secures sensational and absolutely dominant victory **at Aragon GP**"
    retry_title = "Marquez takes **dominant Aragon victory**"
    resp1 = ("news", False, long_title, "Sub 1", "Cap 1")
    resp2 = ("news", False, retry_title, "Sub 2", "Cap 2")

    with patch("app.services.news_copywriter.template_title_fit_chars", side_effect=lambda db, fp, cat: 50 if cat == "news" else None), \
         patch("app.services.news_copywriter._generate_with_fallback", side_effect=[(resp1, "gemini"), (resp2, "gemini")]) as mock_gen:
        res = generate_news_copy(fp, art)
        assert mock_gen.call_count == 2
        # Second call prompt contains correction instruction
        call2_prompt = mock_gen.call_args_list[1][0][0]
        assert "rewrite ONLY the title to at most 50 characters" in call2_prompt
        assert res.title == retry_title
        # Other fields from first answer kept
        assert res.subtitle == "Sub 1"
        assert res.caption == "Cap 1"


def test_generate_news_copy_over_budget_news_retry_still_long_accepted_no_truncate():
    fp = _make_fanpage()
    art = _make_article()
    long_title1 = "Marc Marquez secures sensational and absolutely dominant victory **at Aragon GP**"
    long_title2 = "Marc Marquez secures sensational dominant victory **at Aragon GP**"
    resp1 = ("news", False, long_title1, "Sub 1", "Cap 1")
    resp2 = ("news", False, long_title2, "Sub 2", "Cap 2")

    with patch("app.services.news_copywriter.template_title_fit_chars", side_effect=lambda db, fp, cat: 40 if cat == "news" else None), \
         patch("app.services.news_copywriter._generate_with_fallback", side_effect=[(resp1, "gemini"), (resp2, "gemini")]) as mock_gen:
        res = generate_news_copy(fp, art)
        assert mock_gen.call_count == 2
        assert res.title == long_title2
        assert not res.title.endswith("…")


def test_generate_news_copy_asterisks_not_counted_in_budget():
    fp = _make_fanpage()
    art = _make_article()
    # "Marquez takes victory" is 21 chars. With asterisks "**Marquez takes victory**", raw is 25 chars.
    # Budget is 22 chars.
    title = "**Marquez takes victory**"
    resp1 = ("news", False, title, "Sub 1", "Cap 1")

    with patch("app.services.news_copywriter.template_title_fit_chars", side_effect=lambda db, fp, cat: 22 if cat == "news" else None), \
         patch("app.services.news_copywriter._generate_with_fallback", return_value=(resp1, "gemini")) as mock_gen:
        res = generate_news_copy(fp, art)
        assert mock_gen.call_count == 1
        assert res.title == title


def test_generate_news_copy_quote_toggle_off_never_retries():
    fp = _make_fanpage(quote_shorten_enabled=False)
    art = _make_article()
    long_quote = "I had absolutely no feeling with the front tyre all weekend and crashed out"
    resp1 = ("quote", False, long_quote, "Bagnaia", "Cap with Bagnaia quote")

    with patch("app.services.news_copywriter.template_title_fit_chars", side_effect=lambda db, fp, cat: 50):
        with patch("app.services.news_copywriter._generate_with_fallback", return_value=(resp1, "gemini")) as mock_gen:
            res = generate_news_copy(fp, art)
            assert mock_gen.call_count == 1
            assert res.title == long_quote


def test_generate_news_copy_quote_toggle_on_retries_when_over_budget():
    fp = _make_fanpage(quote_shorten_enabled=True)
    art = _make_article()
    long_quote = "I had absolutely no feeling with the front tyre all weekend and crashed out"
    short_quote = "No feeling with the front tyre… **and crashed out**"
    resp1 = ("quote", False, long_quote, "Bagnaia", "Cap 1")
    resp2 = ("quote", False, short_quote, "Bagnaia", "Cap 2")

    with patch("app.services.news_copywriter.template_title_fit_chars", side_effect=lambda db, fp, cat: 60 if cat == "quote" else 64):
        with patch("app.services.news_copywriter._generate_with_fallback", side_effect=[(resp1, "gemini"), (resp2, "gemini")]) as mock_gen:
            res = generate_news_copy(fp, art)
            assert mock_gen.call_count == 2
            assert res.title == short_quote
