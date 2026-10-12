"""Tests for Section 8: Mode 7 and Mode 8 video title and description length settings."""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from alembic.config import Config
from alembic.script import ScriptDirectory

from app.models.target_fanpages import TargetFanpage
from app.schemas.fanpage import FanpageBase
from app.services import ai_video_fill
from app.services import video_templates
from app.tasks.yt_clip import _clip_caption_prompt, _highlight_prompt


def test_video_length_migration():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    rev = script.get_revision("c80000000011")
    assert rev.down_revision == "c80000000010"
    migration = rev.module

    with patch.object(migration.op, "add_column") as add_column:
        migration.upgrade()

    col_names = [call.args[1].name for call in add_column.call_args_list]
    assert col_names == [
        "yt_clip_title_max_chars",
        "yt_clip_description_max_chars",
        "ai_video_title_max_chars",
        "ai_video_description_max_chars",
    ]


def test_models_and_schemas_defaults():
    cols = {c.name: c for c in TargetFanpage.__table__.columns}
    assert cols["yt_clip_title_max_chars"].default.arg == 60
    assert cols["yt_clip_description_max_chars"].default.arg == 180
    assert cols["ai_video_title_max_chars"].default.arg == 100
    assert cols["ai_video_description_max_chars"].default.arg == 600

    schema = FanpageBase()
    assert schema.yt_clip_title_max_chars == 60
    assert schema.yt_clip_description_max_chars == 180
    assert schema.ai_video_title_max_chars == 100
    assert schema.ai_video_description_max_chars == 600

    # Bounds validation (ge 20, le 5000)
    with pytest.raises(Exception):
        FanpageBase(yt_clip_title_max_chars=10)
    with pytest.raises(Exception):
        FanpageBase(ai_video_description_max_chars=6000)


def test_mode7_uses_fanpage_length_settings():
    fp_custom = SimpleNamespace(
        name="Custom Page",
        caption_language="en",
        mode2_gallery_niches=["f1"],
        yt_clip_per_video=3,
        yt_clip_min_s=60,
        yt_clip_max_s=120,
        yt_clip_min_score=6,
        yt_clip_title_max_chars=80,
        yt_clip_description_max_chars=300,
    )
    meta = SimpleNamespace(title="V Title", channel="V Channel", duration_s=600)
    prompt, _ = _highlight_prompt(fp_custom, None, meta, "00:00:01,000 --> 00:00:05,000\nHello", None, None)
    assert "title max 80 chars" in prompt
    assert "max 80 characters" in prompt

    idea = SimpleNamespace(title="Clip", description="Desc", hook_text="Hook", transcript_excerpt="T")
    video = SimpleNamespace(title="V Title", channel_name="V Channel")
    cap_prompt = _clip_caption_prompt(fp_custom, idea, video)
    assert "max 300 characters before the" in cap_prompt


def test_mode8_uses_fanpage_length_settings_and_validation():
    template = video_templates.get_template("motorsport_tech_explainer")
    idea = SimpleNamespace(
        template_key="motorsport_tech_explainer",
        topic="Rear Wing",
        angle=None,
        research_notes=None,
        variables=None,
    )
    fp_custom = SimpleNamespace(
        id=1,
        name="Formula Custom",
        caption_language="en",
        caption_hashtag_count=3,
        ai_video_title_max_chars=85,
        ai_video_description_max_chars=350,
    )

    fill_prompt = ai_video_fill.build_fill_prompt(template, idea, fp_custom, 2)
    assert "title <= 85 chars, caption <= 350 chars and <= 3 hashtags" in fill_prompt

    # Test parser validation with custom limits
    raw_valid = '{"variables": {"vehicle_preset": "f1_red"}, "title": "A" * 85, "caption": "B" * 350}'
    raw_valid_json = f'{{"variables": {{"vehicle_preset": "f1_red"}}, "title": "{"A" * 80}", "caption": "{"B" * 300} #tag1"}}'
    parsed = ai_video_fill._parse(raw_valid_json, title_max=85, desc_max=350, hashtag_max=3)
    assert parsed["title"] == "A" * 80

    raw_too_long_title = f'{{"variables": {{"vehicle_preset": "f1_red"}}, "title": "{"A" * 90}", "caption": "B"}}'
    with pytest.raises(ValueError, match="at most 85 characters"):
        ai_video_fill._parse(raw_too_long_title, title_max=85, desc_max=350, hashtag_max=3)

    raw_too_long_desc = f'{{"variables": {{"vehicle_preset": "f1_red"}}, "title": "A", "caption": "{"B" * 360}"}}'
    with pytest.raises(ValueError, match="at most 350 characters"):
        ai_video_fill._parse(raw_too_long_desc, title_max=85, desc_max=350, hashtag_max=3)
