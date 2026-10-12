"""Tests for fanpage global caption settings and dropped overrides."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.services.fanpage_caption import CaptionSettings, caption_settings
from app.services.news_copywriter import (
    build_news_copy_prompt,
    build_discussion_news_prompt,
    generate_pinterest_search_keyword,
    generate_pinterest_hashtags,
)
from app.services.ai_caption import build_caption_prompt
from app.tasks.yt_clip import _clip_caption_prompt
from app.services import ai_video_fill
from app.services import video_templates


def test_caption_settings_helper():
    fp = SimpleNamespace(
        caption_tone="witty",
        caption_language="de",
        caption_max_length=350,
        caption_hashtag_count=7,
        caption_must_include=["motorsport", "racing"],
        caption_must_avoid=["clickbait"],
        caption_cta_text="Share this!",
        use_attribution=False,
        caption_attribution_template="credit @{source_username}",
        attribution_position="caption_start",
        caption_custom_prompt="Be concise.",
    )
    cs = caption_settings(fp)
    assert cs.tone == "witty"
    assert cs.language == "de"
    assert cs.max_length == 350
    assert cs.hashtag_count == 7
    assert cs.must_include == ["motorsport", "racing"]
    assert cs.must_avoid == ["clickbait"]
    assert cs.cta_text == "Share this!"
    assert cs.use_attribution is False
    assert cs.attribution_template == "credit @{source_username}"
    assert cs.attribution_position == "caption_start"
    assert cs.custom_prompt == "Be concise."


def test_generators_read_global_caption_settings_only():
    """Verify generators use global settings even when mode2, pinterest, or source fields differ."""
    fp = SimpleNamespace(
        id=99,
        name="Global Test Page",
        # Global fields:
        caption_tone="global_tone_epic",
        caption_language="it",
        caption_max_length=222,
        caption_hashtag_count=4,
        caption_must_include=["global_inc"],
        caption_must_avoid=["global_avoid"],
        caption_cta_text="global_cta_here",
        use_attribution=False,
        caption_attribution_template="via @{source_username}",
        attribution_position="caption_end",
        caption_custom_prompt="global_instruction_unique",
        # Conflicting per-mode fields that MUST NOT be used:
        mode2_caption_tone="stale_m2_tone",
        mode2_caption_max_length=999,
        mode2_caption_hashtag_count=12,
        mode2_caption_cta_text="stale_m2_cta",
        mode2_caption_custom_prompt="stale_m2_custom_prompt",
        mode2_source_attribution=True,
        pinterest_hashtag_count=19,
        pinterest_custom_prompt="stale_pin_custom_prompt",
        mode2_gallery_niches=["f1"],
        discussion_label_mode="both",
        mode2_title_max_chars=80,
    )

    # 1. News copywriter prompt
    source = SimpleNamespace(
        name="SourceOrg",
        caption_tone="source_tone_override",
        caption_max_length=888,
        caption_hashtag_count=15,
        caption_cta_text="source_cta",
        caption_custom_prompt="source_prompt",
    )
    article = SimpleNamespace(
        news_source=source,
        scraped_title="Headline Title",
        scraped_content="Article body content",
    )
    news_prompt = build_news_copy_prompt(fp, article)
    assert "- Language: it" in news_prompt
    assert "- Tone: global_tone_epic" in news_prompt
    assert "- Maximum length: 222 characters" in news_prompt
    assert "EXACTLY 4 relevant, specific hashtags" in news_prompt
    assert "End with call-to-action: global_cta_here" in news_prompt
    assert "Additional notes: global_instruction_unique" in news_prompt
    assert "Source: SourceOrg" not in news_prompt # global use_attribution is False
    assert "stale_m2" not in news_prompt
    assert "source_tone_override" not in news_prompt

    # 2. Discussion news prompt
    disc_prompt = build_discussion_news_prompt(fp, article)
    assert "- Language: it" in disc_prompt
    assert "- Tone: global_tone_epic" in disc_prompt
    assert "- Maximum length: 222 characters" in disc_prompt
    assert "EXACTLY 4 specific" in disc_prompt
    assert "Call-to-action: global_cta_here" in disc_prompt
    assert "Additional notes: global_instruction_unique" in disc_prompt
    assert "stale_m2" not in disc_prompt

    # 3. AI caption prompt
    cap_prompt = build_caption_prompt(fp, "ig_user", "original ig caption", source=source)
    assert "- Language: it" in cap_prompt
    assert "- Tone: global_tone_epic" in cap_prompt
    assert "- Maximum length: 222 characters" in cap_prompt
    assert "- Include 4 relevant hashtags" in cap_prompt
    assert "- End with call-to-action: global_cta_here" in cap_prompt
    assert "- Additional notes: global_instruction_unique" in cap_prompt
    assert "source_tone_override" not in cap_prompt

    # 4. YouTube clip prompt
    idea = SimpleNamespace(
        title="Clip Title",
        description="Clip Desc",
        hook_text="Clip Hook",
        transcript_excerpt="Clip Transcript",
    )
    video = SimpleNamespace(title="YT Video", channel_name="YT Channel")
    yt_prompt = _clip_caption_prompt(fp, idea, video)
    assert "- Language: it" in yt_prompt
    assert "- Tone: global_tone_epic" in yt_prompt
    assert "exactly 4 hashtags" in yt_prompt
    assert "Call-to-action: global_cta_here" in yt_prompt
    assert "Additional notes: global_instruction_unique" in yt_prompt
    assert "stale_m2" not in yt_prompt

    # 5. Pinterest search keyword & hashtags
    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(('{"keyword": "Ayrton"}',), "gemini")) as mock_ai:
        # Pinterest keyword prompt uses pinterest_custom_prompt for content sourcing
        try:
            generate_pinterest_search_keyword(fp)
        except Exception:
            pass
        called_prompt = mock_ai.call_args[0][0]
        assert "stale_pin_custom_prompt" in called_prompt
        assert "global_instruction_unique" not in called_prompt

    with patch("app.services.news_copywriter._generate_with_fallback", return_value=(('{"hashtags": "#a #b #c #d"}',), "gemini")) as mock_ai:
        try:
            generate_pinterest_hashtags(fp, "Title", "Desc")
        except Exception:
            pass
        called_prompt = mock_ai.call_args[0][0]
        assert "EXACTLY 4 specific" in called_prompt
        assert "global_instruction_unique" in called_prompt
        assert "19" not in called_prompt
        assert "stale_pin_custom_prompt" not in called_prompt

    # 6. Mode 8 fill prompt caption length & hashtags
    template = video_templates.get_template("motorsport_tech_explainer")
    v_idea = SimpleNamespace(
        template_key="motorsport_tech_explainer",
        topic="Aero",
        angle=None,
        research_notes=None,
        variables=None,
    )
    fill_prompt = ai_video_fill.build_fill_prompt(template, v_idea, fp, 2)
    assert "caption <= 600 chars and <= 4 hashtags" in fill_prompt
    assert "Narration must be in Italian." in fill_prompt
