import pytest
from datetime import datetime
from types import SimpleNamespace
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock

from app.services.ig_mode import ig_mode, apply_ig_mode
from app.tasks.fan_out import fanout_link
from app.models.publish_jobs import PublishJob
from app.models.target_fanpages import TargetFanpage


def test_ig_mode_round_trips():
    # 1. repost: ig_recreate_enabled=False
    fp = SimpleNamespace(ig_recreate_enabled=False, visual_engine="off")
    assert ig_mode(fp) == "repost"

    # apply recreate_template
    apply_ig_mode(fp, "recreate_template")
    assert fp.ig_recreate_enabled is True
    assert fp.visual_engine == "off"
    assert ig_mode(fp) == "recreate_template"

    # apply recreate_chatgpt
    apply_ig_mode(fp, "recreate_chatgpt")
    assert fp.ig_recreate_enabled is True
    assert fp.visual_engine == "chatgpt"
    assert ig_mode(fp) == "recreate_chatgpt"

    # auto / gflow maps to recreate_chatgpt when reading
    fp.visual_engine = "auto"
    assert ig_mode(fp) == "recreate_chatgpt"
    fp.visual_engine = "gflow"
    assert ig_mode(fp) == "recreate_chatgpt"

    # apply repost
    apply_ig_mode(fp, "repost")
    assert fp.ig_recreate_enabled is False
    assert ig_mode(fp) == "repost"

    with pytest.raises(ValueError, match="Unknown IG mode"):
        apply_ig_mode(fp, "invalid_mode")


def test_fan_out_routing_per_mode_and_source_override():
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None  # no existing job

    post = SimpleNamespace(id=101)

    # Case A: Fanpage in repost mode, no source override -> routes to plain repost
    fp_repost = SimpleNamespace(id=1, mode1_ig_repost_enabled=True, ig_recreate_enabled=False, visual_engine="off")
    link_a = SimpleNamespace(fanpage_id=1, fanpage=fp_repost, ig_recreate_enabled=None)

    with patch("app.tasks.ai_generator.generate_caption_for_job.apply_async") as mock_cap, \
         patch("app.tasks.ig_recreate.recreate_post_for_fanpage.apply_async") as mock_rec:
        res = fanout_link(db, post, link_a, countdown=0)
        assert res is True
        assert db.add.called
        assert mock_cap.called
        assert not mock_rec.called

    # Case B: Fanpage in recreate mode, no source override -> routes to recreate
    db.reset_mock()
    fp_rec = SimpleNamespace(id=2, mode1_ig_repost_enabled=True, ig_recreate_enabled=True, visual_engine="off")
    link_b = SimpleNamespace(fanpage_id=2, fanpage=fp_rec, ig_recreate_enabled=None)

    with patch("app.tasks.ai_generator.generate_caption_for_job.apply_async") as mock_cap, \
         patch("app.tasks.ig_recreate.recreate_post_for_fanpage.apply_async") as mock_rec:
        res = fanout_link(db, post, link_b, countdown=0)
        assert res is True
        assert mock_rec.called
        assert not mock_cap.called

    # Case C: Fanpage in recreate mode, source override = False -> routes to plain repost
    db.reset_mock()
    link_c = SimpleNamespace(fanpage_id=2, fanpage=fp_rec, ig_recreate_enabled=False)

    with patch("app.tasks.ai_generator.generate_caption_for_job.apply_async") as mock_cap, \
         patch("app.tasks.ig_recreate.recreate_post_for_fanpage.apply_async") as mock_rec:
        res = fanout_link(db, post, link_c, countdown=0)
        assert res is True
        assert mock_cap.called
        assert not mock_rec.called

    # Case D: Fanpage in repost mode, source override = True -> routes to recreate
    db.reset_mock()
    link_d = SimpleNamespace(fanpage_id=1, fanpage=fp_repost, ig_recreate_enabled=True)

    with patch("app.tasks.ai_generator.generate_caption_for_job.apply_async") as mock_cap, \
         patch("app.tasks.ig_recreate.recreate_post_for_fanpage.apply_async") as mock_rec:
        res = fanout_link(db, post, link_d, countdown=0)
        assert res is True
        assert mock_rec.called
        assert not mock_cap.called


def test_update_fanpage_api_with_ig_mode():
    from app.main import app
    from app.api.deps import get_current_user, get_db

    fake_fp = TargetFanpage(
        id=99,
        repliz_account_id="rep_99",
        name="Test FP",
        platform_type="facebook",
        is_connected=True,
        is_active=True,
        timezone="Europe/London",
        caption_tone="engaging",
        caption_language="en",
        caption_max_length=500,
        caption_hashtag_count=5,
        caption_must_include=[],
        caption_must_avoid=[],
        caption_cta_text="",
        use_attribution=True,
        caption_attribution_template="via @{source_username}",
        attribution_position="caption_end",
        caption_custom_prompt="",
        publish_mode="manual_review",
        publish_daily_limit=35,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=False,
        mode2_publish_mode="manual_review",
        mode2_gallery_keywords=[],
        mode2_gallery_niches=[],
        mode2_caption_tone="informative",
        mode2_caption_language="en",
        mode2_caption_max_length=500,
        mode2_caption_hashtag_count=5,
        mode2_caption_cta_text="",
        mode2_caption_custom_prompt="",
        mode2_title_max_chars=80,
        mode2_source_attribution=True,
        mode2_editorial_gate_enabled=False,
        ig_recreate_enabled=False,
        ig_recreate_smart_layout=False,
        design_expand=False,
        discussion_enabled=False,
        discussion_publish_mode="manual_review",
        discussion_daily_count=2,
        discussion_topic_mode="both",
        discussion_label_mode="both",
        pinterest_enabled=False,
        pinterest_publish_mode="manual_review",
        pinterest_daily_count=2,
        pinterest_source_mode="both",
        pinterest_custom_prompt="",
        pinterest_hashtag_count=5,
        pinterest_allow_low_quality=False,
        facebook_photo_enabled=False,
        facebook_photo_publish_mode="manual_review",
        facebook_photo_daily_count=2,
        yt_clip_enabled=False,
        yt_clip_publish_mode="manual_review",
        yt_clip_daily_count=2,
        yt_clip_min_s=60,
        yt_clip_max_s=120,
        yt_clip_per_video=3,
        yt_clip_min_score=6,
        yt_clip_max_video_age_days=7,
        yt_clip_captions=True,
        yt_clip_watermark=True,
        yt_clip_action_crop="smart",
        yt_clip_title_max_chars=60,
        yt_clip_description_max_chars=180,
        ai_video_enabled=False,
        ai_video_publish_mode="manual_review",
        ai_video_daily_count=1,
        ai_video_clips=2,
        ai_video_title_max_chars=100,
        ai_video_description_max_chars=600,
        ai_video_logo_enabled=True,
        ai_video_logo_size_pct=14,
        ai_video_logo_position="top_right",
        ai_video_logo_margin_pct=3,
        ai_video_captions_enabled=True,
        ai_video_caption_size=76,
        ai_video_caption_bottom_pct=29,
        ai_video_caption_highlight="#FFE500",
        radar_enabled=False,
        radar_niches=[],
        radar_shadow=True,
        radar_min_likes=1000,
        radar_confirm_ratio=1.5,
        radar_fast_ratio=2.0,
        radar_fast_window_min=60,
        radar_shelf_news_h=72,
        radar_shelf_evergreen_h=168,
        radar_burst_enabled=True,
        radar_daily_max=3,
        visual_engine="off",
        news_visual_engine="off",
        fb_visual_engine="off",
        quote_shorten_enabled=False,
        created_at=datetime.utcnow(),
    )

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = fake_fp

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="admin")
    app.dependency_overrides[get_db] = lambda: mock_db

    client = TestClient(app)

    try:
        # 1. Update to recreate_chatgpt
        res = client.put("/fanpages/99", json={"ig_mode": "recreate_chatgpt"})
        assert res.status_code == 200
        data = res.json()
        assert data["ig_mode"] == "recreate_chatgpt"
        assert fake_fp.ig_recreate_enabled is True
        assert fake_fp.visual_engine == "chatgpt"

        # 2. Update to recreate_template
        res = client.put("/fanpages/99", json={"ig_mode": "recreate_template"})
        assert res.status_code == 200
        data = res.json()
        assert data["ig_mode"] == "recreate_template"
        assert fake_fp.ig_recreate_enabled is True
        assert fake_fp.visual_engine == "off"

        # 3. Update to repost
        res = client.put("/fanpages/99", json={"ig_mode": "repost"})
        assert res.status_code == 200
        data = res.json()
        assert data["ig_mode"] == "repost"
        assert fake_fp.ig_recreate_enabled is False

    finally:
        app.dependency_overrides.clear()
