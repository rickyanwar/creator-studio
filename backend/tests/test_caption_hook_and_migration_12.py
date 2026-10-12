import pytest
from unittest.mock import patch
from types import SimpleNamespace
from alembic.config import Config
from alembic.script import ScriptDirectory
from app.services.news_copywriter import build_news_copy_prompt, build_discussion_news_prompt
from app.services.ai_caption import build_caption_prompt
from app.tasks.yt_clip import _clip_caption_prompt
from app.services import ai_video_fill, video_templates
from app.services.design_images import vision_check_pin_description, vision_identify_pin_subject


HOOK_RULE = "The FIRST sentence is the hook a reader sees before 'See more' on Facebook mobile: at most 125 characters, concrete and specific (who + what), no generic opener."


def test_caption_length_600_migration():
    config = Config("alembic.ini")
    script = ScriptDirectory.from_config(config)
    rev = script.get_revision("c80000000012")
    assert rev.down_revision == "c80000000011"
    migration = rev.module

    # Test upgrade: creates backup table, inserts current values, updates target_fanpages
    with patch.object(migration.op, "create_table") as mock_create, \
         patch.object(migration.op, "execute") as mock_exec:
        migration.upgrade()
        assert mock_create.called
        assert mock_create.call_args[0][0] == "caption_settings_backup_20261012"
        exec_sqls = [call[0][0] for call in mock_exec.call_args_list]
        assert any("INSERT INTO caption_settings_backup_20261012" in sql for sql in exec_sqls)
        assert any("UPDATE target_fanpages SET caption_max_length = 600, caption_hashtag_count = 3" in sql for sql in exec_sqls)

    # Test downgrade: restores values from backup table, drops backup table
    with patch.object(migration.op, "drop_table") as mock_drop, \
         patch.object(migration.op, "execute") as mock_exec:
        migration.downgrade()
        exec_sqls = [call[0][0] for call in mock_exec.call_args_list]
        assert any("UPDATE target_fanpages tf" in sql and "FROM caption_settings_backup_20261012 b" in sql for sql in exec_sqls)
        assert mock_drop.called
        assert mock_drop.call_args[0][0] == "caption_settings_backup_20261012"


def test_all_caption_generators_contain_hook_rule():
    fp = SimpleNamespace(
        id=1,
        name="Test Fanpage",
        caption_language="en",
        mode2_caption_language="en",
        caption_tone="informative",
        caption_max_length=600,
        caption_hashtag_count=3,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_cta_text="",
        use_attribution=False,
        caption_attribution_template="",
        attribution_position="end",
        caption_custom_prompt="",
        mode2_source_attribution=False,
        mode2_gallery_niches=["motorsport"],
        mode2_title_max_chars=90,
        ai_video_title_max_chars=100,
        ai_video_description_max_chars=600,
        yt_clip_description_max_chars=180,
    )
    article = SimpleNamespace(
        news_source=None,
        scraped_title="Race Report",
        scraped_content="Race content body",
    )

    # 1. news_copywriter
    news_prompt = build_news_copy_prompt(fp, article)
    assert HOOK_RULE in news_prompt

    # 2. discussion prompt
    disc_prompt = build_discussion_news_prompt(fp, article)
    assert HOOK_RULE in disc_prompt

    # 3. ai_caption (used directly and by ig_recreate, facebook_photo)
    cap_prompt = build_caption_prompt(fp, "testuser", "sample orig caption")
    assert HOOK_RULE in cap_prompt

    # 4. yt_clip
    idea = SimpleNamespace(title="Clip", description="Desc", hook_text="", transcript_excerpt="")
    video = SimpleNamespace(title="Vid", channel_name="Chan")
    yt_prompt = _clip_caption_prompt(fp, idea, video)
    assert HOOK_RULE in yt_prompt

    # 5. ai_video_fill
    template = video_templates.get_template("motorsport_tech_explainer")
    v_idea = SimpleNamespace(template_key="motorsport_tech_explainer", topic="Aero", angle="", research_notes="", variables=None)
    fill_prompt = ai_video_fill.build_fill_prompt(template, v_idea, fp, 2)
    assert HOOK_RULE in fill_prompt

    # 6. Pinterest copy prompts (vision_check_pin_description & vision_identify_pin_subject)
    with patch("app.services.design_images._vision_chat", return_value='{"valid": true, "title": "t", "description": "d"}') as mock_vchat:
        vision_check_pin_description(b"dummy_bytes", "claimed desc")
        prompt_content = mock_vchat.call_args[0][0][0]["text"]
        assert HOOK_RULE in prompt_content

    with patch("app.services.design_images._vision_chat", return_value='{"identified": true, "title": "t", "description": "d"}') as mock_vchat:
        vision_identify_pin_subject(b"dummy_bytes")
        prompt_content = mock_vchat.call_args[0][0][0]["text"]
        assert HOOK_RULE in prompt_content
