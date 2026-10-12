"""Tests for fanpage language unification and Mode 8 video narration language."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.services.fanpage_language import fanpage_language, language_name
from app.services import video_templates
from app.services import ai_video_fill


def test_fanpage_language_helpers():
    fp_default = SimpleNamespace(name="Default")
    assert fanpage_language(fp_default) == "en"

    fp_fr = SimpleNamespace(name="GP Weekend", caption_language="fr")
    assert fanpage_language(fp_fr) == "fr"

    fp_empty = SimpleNamespace(name="Empty", caption_language="")
    assert fanpage_language(fp_empty) == "en"

    # language_name mapping
    assert language_name("en") == "English"
    assert language_name("fr") == "French"
    assert language_name("es") == "Spanish"
    assert language_name("de") == "German"
    assert language_name("it") == "Italian"
    assert language_name("pt") == "Portuguese"
    assert language_name("id") == "Indonesian"
    assert language_name("nl") == "Dutch"
    assert language_name("unknown") == "unknown"
    assert language_name(None) == "English"


def test_mode8_word_budgets_and_prompts():
    template = video_templates.get_template("motorsport_tech_explainer")
    clip = template.raw["clip_templates"][0]

    # English unchanged 22-24
    assert video_templates.get_narration_budget(clip, "en") == "22-24"

    # French factor 0.82: 22*0.82=18.04 -> 18, 24*0.82=19.68 -> 20 => 18-20
    assert video_templates.get_narration_budget(clip, "fr") == "18-20"

    # German factor 0.78: 22*0.78=17.16 -> 17, 24*0.78=18.72 -> 19 => 17-19
    assert video_templates.get_narration_budget(clip, "de") == "17-19"

    idea = SimpleNamespace(
        template_key="motorsport_tech_explainer",
        topic="Rear Wing DRS",
        angle=None,
        research_notes=None,
        variables=None,
    )

    # FR fanpage: prompt says French and 18-20 budget
    fp_fr = SimpleNamespace(id=1, name="GP Weekend", caption_language="fr")
    prompt_fr = ai_video_fill.build_fill_prompt(template, idea, fp_fr, 2)
    assert "Narration must be in French." in prompt_fr
    assert '"narration_1": "18-20"' in prompt_fr
    assert "Title and caption must be in fr" in prompt_fr

    # EN fanpage: prompt says English and 22-24 budget
    fp_en = SimpleNamespace(id=2, name="Formula Archives", caption_language="en")
    prompt_en = ai_video_fill.build_fill_prompt(template, idea, fp_en, 2)
    assert "Narration must be in English." in prompt_en
    assert '"narration_1": "22-24"' in prompt_en
    assert "Title and caption must be in en" in prompt_en


def test_mode8_render_clips_budget_validation_and_narrator():
    template = video_templates.get_template("motorsport_tech_explainer")

    # In French, budget is 18-20 (allowed range: lo - 6 = 12 up to hi = 20)
    # 18 words narration is valid for French
    fr_words = "Un deux trois quatre cinq six sept huit neuf dix onze douze treize quatorze quinze seize dix-sept dix-huit"
    assert len(fr_words.split()) == 18

    vars_fr = {
        "vehicle_preset": "f1_red",
        "narrator_preset": "EN-DOC",
        "part": "aileron arriere",
        "mechanism_action": "s'ouvre pour reduire la trainee",
        "mechanism_details": "verin hydraulique et volet superieur",
        "parts_list": "verin hydraulique et volet",
        "effect_visual": "flux d'air visible",
        "final_state": "fermeture",
        "payoff_action": "acceleration maximale",
        "narration_1": fr_words,
        "narration_2": fr_words,
    }
    rendered_fr = video_templates.render_clips(template, vars_fr, 2, lang_code="fr")
    assert "speaking natural, native-sounding French" in rendered_fr[0]["prompt"]
    assert "says in French:" in rendered_fr[0]["prompt"]

    # If 22 words passed in French, it should fail (max is 20)
    vars_fr_too_long = dict(vars_fr, narration_1="word " * 22)
    with pytest.raises(ValueError, match="narration_1 must contain 12–20 words"):
        video_templates.render_clips(template, vars_fr_too_long, 2, lang_code="fr")


def test_migration_c80000000010():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    rev = script.get_revision("c80000000010")
    assert rev.down_revision == "c80000000009"
    migration = rev.module

    with patch.object(migration.op, "execute") as mock_exec:
        migration.upgrade()
    assert mock_exec.called
    sql = mock_exec.call_args[0][0]
    assert "UPDATE target_fanpages SET mode2_caption_language = caption_language" in sql


def test_generators_use_fanpage_language():
    from app.services.news_copywriter import build_news_copy_prompt, build_discussion_news_prompt
    from app.services.ai_caption import build_caption_prompt
    from app.tasks.ig_recreate import _rewrite_news_title, _translate_quote
    from app.services.facebook_photo_source import _localize_discussion, _localize_quote
    from app.tasks.yt_clip import _clip_caption_prompt

    fp = SimpleNamespace(
        id=1,
        name="GP Weekend",
        caption_language="fr",
        mode2_caption_language="en", # deliberately different to prove caption_language wins
        mode2_source_attribution=False,
        mode2_caption_tone="pro",
        mode2_caption_max_length=400,
        mode2_caption_hashtag_count=3,
        mode2_caption_cta_text="",
        mode2_caption_custom_prompt="",
        mode2_title_max_chars=80,
        mode2_gallery_niches=["f1"],
        use_attribution=False,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_tone="sharp",
        caption_max_length=500,
        caption_hashtag_count=5,
        caption_cta_text="",
        caption_custom_prompt="",
        discussion_label_mode="both",
    )

    # 1. news_copywriter
    article = SimpleNamespace(
        news_source=SimpleNamespace(name="L'Equipe", caption_language="es"), # override ignored for language
        scraped_title="Ferrari rapide",
        scraped_content="Contenu de test",
    )
    news_prompt = build_news_copy_prompt(fp, article)
    assert "- Language: fr" in news_prompt

    # 2. discussion
    disc_prompt = build_discussion_news_prompt(fp, article)
    assert "- Language: fr" in disc_prompt

    # 3. ai_caption
    cap_prompt = build_caption_prompt(fp, "source_user", "orig text")
    assert "- Language: fr" in cap_prompt

    # 4. ig_recreate
    with patch("app.services.ai_caption.generate_caption", return_value=("Titre 10", "gemini")) as mock_ai:
        _rewrite_news_title("Titre 10", "caption", fp)
        assert "- Language: fr" in mock_ai.call_args[0][0]

    with patch("app.services.ai_caption.generate_caption", return_value=("Citation", "gemini")) as mock_ai:
        _translate_quote("Original quote", fp)
        assert "Translate this quote to fr" in mock_ai.call_args[0][0]

    # 5. facebook_photo_source
    with patch("app.services.ai_caption.generate_caption", return_value=("Ligne", "gemini")) as mock_ai:
        _localize_discussion("Debat", fp)
        assert "- Language: fr" in mock_ai.call_args[0][0]

    with patch("app.services.ai_caption.generate_caption", return_value=("Citation", "gemini")) as mock_ai:
        _localize_quote("Original quote", fp)
        assert "Translate this quote to fr" in mock_ai.call_args[0][0]

    # 6. yt_clip caption prompt
    idea = SimpleNamespace(
        title="Super clip",
        description="Desc",
        hook_text="Hook",
        transcript_excerpt="Excerpt",
    )
    video = SimpleNamespace(title="Video title", channel_name="Ch")
    yt_prompt = _clip_caption_prompt(fp, idea, video)
    assert "- Language: fr" in yt_prompt
