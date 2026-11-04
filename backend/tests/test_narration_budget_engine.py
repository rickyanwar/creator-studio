"""Voice budgets and Muse intake use the page's effective prompt engine."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.models.target_fanpages import TargetFanpage
from app.services import research_desk
from app.services.ai_video_narration import prompt_engine_for
from app.services.video_templates import get_narration_budget, get_template, render_clips


KEY = "health_3d_explainer"


def page(engine):
    return TargetFanpage(
        id=12, repliz_account_id="budget-test", name="Health channel", is_active=True,
        ai_video_enabled=True, mode2_gallery_niches=["health"], caption_language="en",
        target_country="GB", ai_video_templates=[KEY], ai_video_clips=4, ai_video_clips_min=2,
        ai_video_min_score=70, ai_video_narration={KEY: {"engine": engine, "voice": None}},
    )


def variables():
    values = dict(get_template(KEY).raw["example"]["variables"])
    for name in values:
        if name.startswith("narration_"):
            values[name] = " ".join(["word"] * 20)
    return values


def test_health_narration_budget_by_engine():
    clip = get_template(KEY).raw["clip_templates"][0]
    assert get_narration_budget(clip, "en", "flow") == "18-22"
    assert get_narration_budget(clip, "en", "tts") == "18-22"
    assert get_narration_budget(clip, "en", "asmr") == "14-18"


@pytest.mark.parametrize("engine", ["flow", "tts", "asmr"])
def test_health_render_twenty_words(engine):
    template = get_template(KEY)
    if engine == "asmr":
        with pytest.raises(ValueError, match="narration_1 must contain 8–18 words"):
            render_clips(template, variables(), 4, narration_engine=engine)
    else:
        assert len(render_clips(template, variables(), 4, narration_engine=engine)) == 4


@pytest.mark.parametrize("engine,target", [("tts", "18-22"), ("asmr", "14-18")])
def test_research_narration_words_follow_page_engine(engine, target):
    template = get_template(KEY)
    fp = page(engine)
    actual = research_desk._narration_words(template, "en", prompt_engine_for(fp, template, 4))
    assert actual["narration_1"]["target"] == target
    by_clips = research_desk._narration_words_by_clips(
        template, "en", lambda n: prompt_engine_for(fp, template, n),
    )
    for n in template.clip_options:
        assert by_clips[str(n)]["narration_1"]["target"] == ("18-22" if n == 1 else target)


@pytest.mark.parametrize("engine,target", [("tts", "18-22"), ("asmr", "14-18")])
def test_research_brief_passes_page_engine(monkeypatch, engine, target):
    fp = page(engine)
    row = SimpleNamespace(ai_video_researcher="muse", muse_research_mode="pilot", muse_pilot_packs=3)
    monkeypatch.setattr(research_desk, "get_settings_row", lambda db: row)
    monkeypatch.setattr(research_desk, "effective_references", lambda row: {})
    monkeypatch.setattr(research_desk, "research_channels", lambda db: {"health": [fp]})
    monkeypatch.setattr(research_desk, "channel_need", lambda *args: 1)
    monkeypatch.setattr(research_desk, "waiting_ideas", lambda *args: 0)
    monkeypatch.setattr(research_desk, "bank_target", lambda *args: 1)
    monkeypatch.setattr(research_desk, "recent_topics", lambda *args: [])
    brief = research_desk.build_brief(MagicMock())
    template = brief["niches"][0]["channels"][0]["templates"][KEY]
    assert template["narration_words"]["narration_1"]["target"] == target
    assert template["narration_words_by_clips"]["4"]["narration_1"]["target"] == target


@pytest.mark.parametrize("engine", ["tts", "asmr"])
def test_muse_intake_checks_page_engine(monkeypatch, engine):
    fp = page(engine)
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = fp
    monkeypatch.setattr(research_desk, "recent_topics", lambda *args: [])
    variant = {
        "channel_id": fp.id, "angle": "Explain healthy tissue", "variables": variables(),
        "title": "How tissue works", "caption": "Watch healthy tissue move.",
    }
    errors, checked_page = research_desk._check_variant(
        db, variant, {"niche": "health", "clips": 4, "topic": "Healthy tissue", "score": 82},
        get_template(KEY), SimpleNamespace(), {fp.id: 1}, [], used_subjects_cache=[],
    )
    assert checked_page is fp
    if engine == "asmr":
        assert errors == ["narration_1 must contain 8–18 words"]
    else:
        assert errors == []
