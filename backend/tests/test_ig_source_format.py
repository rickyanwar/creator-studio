from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from app.services.ig_mode import (
    format_from_source,
    source_from_format,
    resolve_effective_engine,
)
from app.models.fanpage_sources import FanpageSource
from app.models.target_fanpages import TargetFanpage
from app.models.ig_sources import IGSource


def test_format_mapping_bidirectional():
    """Verify bidirectional mapping between DB fields and 4 format choices."""
    # Source -> Format
    assert format_from_source(None, None) == "follow"
    assert format_from_source(False, None) == "repost"
    assert format_from_source(False, "chatgpt") == "repost"
    assert format_from_source(True, "template") == "template"
    assert format_from_source(True, "chatgpt") == "chatgpt"
    assert format_from_source(True, None) == "template"

    # Format -> Source
    assert source_from_format("follow") == (None, None)
    assert source_from_format("Follow brand default") == (None, None)
    assert source_from_format("follow_brand") == (None, None)

    assert source_from_format("repost") == (False, None)
    assert source_from_format("Repost as-is") == (False, None)

    assert source_from_format("template") == (True, "template")
    assert source_from_format("Recreate with template") == (True, "template")
    assert source_from_format("recreate_template") == (True, "template")

    assert source_from_format("chatgpt") == (True, "chatgpt")
    assert source_from_format("Recreate with ChatGPT edit") == (True, "chatgpt")
    assert source_from_format("recreate_chatgpt") == (True, "chatgpt")

    # Invalid values
    with pytest.raises(ValueError):
        source_from_format("invalid_mode")
    with pytest.raises(ValueError):
        source_from_format("")


def test_resolve_effective_engine():
    """Per-source override takes precedence over brand visual_engine."""
    fp_chatgpt = SimpleNamespace(visual_engine="chatgpt")
    fp_off = SimpleNamespace(visual_engine="off")

    # Link overrides brand
    link_tpl = SimpleNamespace(visual_engine_override="template")
    link_gpt = SimpleNamespace(visual_engine_override="chatgpt")
    link_none = SimpleNamespace(visual_engine_override=None)

    assert resolve_effective_engine(fp_chatgpt, link_tpl) == "off"
    assert resolve_effective_engine(fp_off, link_gpt) == "chatgpt"

    # Link None inherits brand
    assert resolve_effective_engine(fp_chatgpt, link_none) == "chatgpt"
    assert resolve_effective_engine(fp_off, link_none) == "off"
    assert resolve_effective_engine(fp_chatgpt, None) == "chatgpt"


def test_api_set_source_format_and_validation():
    """Endpoint /{fanpage_id}/sources/{ig_source_id}/recreate accepts format and validates."""
    from app.main import app
    from app.api.deps import get_current_user, get_db

    link = FanpageSource(
        id=10,
        fanpage_id=1,
        ig_source_id=2,
        is_active=True,
        ig_recreate_enabled=None,
        visual_engine_override=None,
    )

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.return_value.first.return_value = link

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="admin")
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)

    try:
        # 1. Update to template
        res = client.put("/fanpages/1/sources/2/recreate", json={"format": "template"})
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "template"
        assert data["ig_recreate_enabled"] is True
        assert data["visual_engine_override"] == "template"
        assert link.ig_recreate_enabled is True
        assert link.visual_engine_override == "template"

        # 2. Update to chatgpt
        res = client.put("/fanpages/1/sources/2/recreate", json={"format": "chatgpt"})
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "chatgpt"
        assert data["ig_recreate_enabled"] is True
        assert data["visual_engine_override"] == "chatgpt"
        assert link.ig_recreate_enabled is True
        assert link.visual_engine_override == "chatgpt"

        # 3. Update to repost
        res = client.put("/fanpages/1/sources/2/recreate", json={"format": "repost"})
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "repost"
        assert data["ig_recreate_enabled"] is False
        assert data["visual_engine_override"] is None
        assert link.ig_recreate_enabled is False
        assert link.visual_engine_override is None

        # 4. Update to follow
        res = client.put("/fanpages/1/sources/2/recreate", json={"format": "follow"})
        assert res.status_code == 200
        data = res.json()
        assert data["format"] == "follow"
        assert data["ig_recreate_enabled"] is None
        assert data["visual_engine_override"] is None
        assert link.ig_recreate_enabled is None
        assert link.visual_engine_override is None

        # 5. Legacy backward compatibility (ig_recreate_enabled)
        res = client.put("/fanpages/1/sources/2/recreate", json={"ig_recreate_enabled": True})
        assert res.status_code == 200
        data = res.json()
        assert data["ig_recreate_enabled"] is True
        assert data["format"] == "template"

        # 6. Validation error on bad format
        res = client.put("/fanpages/1/sources/2/recreate", json={"format": "invalid_format"})
        assert res.status_code == 422

    finally:
        app.dependency_overrides.clear()


def _make_full_fanpage(fp_id: int = 5, **kwargs):
    defaults = dict(
        id=fp_id,
        repliz_account_id=f"rep_{fp_id}",
        name=f"Test FP {fp_id}",
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
    defaults.update(kwargs)
    return TargetFanpage(**defaults)


def test_api_get_fanpage_returns_format():
    """GET /fanpages/{fanpage_id} returns format and visual_engine_override on ig_sources."""
    from app.main import app
    from app.api.deps import get_current_user, get_db

    fake_fp = _make_full_fanpage(5)
    link = FanpageSource(
        id=1,
        fanpage_id=5,
        ig_source_id=12,
        is_active=True,
        ig_recreate_enabled=True,
        visual_engine_override="chatgpt",
        trigger="every_post",
    )
    ig_src = IGSource(
        id=12,
        ig_username="racer_x",
        is_active=True,
        album_image_indices=[1],
    )

    mock_db = MagicMock()
    def mock_query(model):
        m = MagicMock()
        if model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fake_fp
        elif model == FanpageSource:
            m.filter_by.return_value.all.return_value = [link]
        elif model == IGSource:
            m.filter.return_value.all.return_value = [ig_src]
        else:
            m.filter_by.return_value.all.return_value = []
            m.filter_by.return_value.first.return_value = None
            m.filter.return_value.all.return_value = []
        return m
    mock_db.query.side_effect = mock_query

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1, username="admin")
    app.dependency_overrides[get_db] = lambda: mock_db
    client = TestClient(app)

    try:
        res = client.get("/fanpages/5")
        assert res.status_code == 200
        data = res.json()
        assert len(data["ig_sources"]) == 1
        src_out = data["ig_sources"][0]
        assert src_out["id"] == 12
        assert src_out["format"] == "chatgpt"
        assert src_out["visual_engine_override"] == "chatgpt"
        assert src_out["ig_recreate_enabled"] is True
    finally:
        app.dependency_overrides.clear()


def test_recreate_post_for_fanpage_engine_routing(tmp_path):
    """recreate_post_for_fanpage queues to visual queue if source format is chatgpt, delay if template."""
    import app.tasks.ig_recreate as ig_mod
    from app.tasks.ig_recreate import recreate_post_for_fanpage
    from app.services.ig_content_classifier import PostAnalysis

    img = tmp_path / "test.jpg"
    img.write_bytes(b"dummy")

    post = SimpleNamespace(
        id=101,
        ig_source_id=20,
        image_local_paths=[str(img)],
        original_caption="Test caption",
    )
    # Fanpage has visual_engine off, but source link overrides to chatgpt
    fp = SimpleNamespace(
        id=1,
        name="FP 1",
        ig_recreate_enabled=False,
        visual_engine="off",
        radar_niches=[],
        mode2_gallery_niches=[],
    )
    link = SimpleNamespace(
        fanpage_id=1,
        ig_source_id=20,
        ig_recreate_enabled=True,
        visual_engine_override="chatgpt",
    )

    mock_db = MagicMock()
    mock_db.query.return_value.filter_by.side_effect = lambda **kw: (
        MagicMock(first=lambda: None) if "post_id" in kw and "fanpage_id" in kw  # no existing job
        else MagicMock(first=lambda: post) if "id" in kw and kw["id"] == 101
        else MagicMock(first=lambda: fp) if "id" in kw and kw["id"] == 1
        else MagicMock(first=lambda: link) if "fanpage_id" in kw and "ig_source_id" in kw
        else MagicMock(first=lambda: SimpleNamespace(ig_username="source_user")) if "id" in kw and kw["id"] == 20
        else MagicMock(first=lambda: SimpleNamespace(id=1, category="quote"))
    )

    analysis = PostAnalysis(
        type="quote",
        text="A great quote",
        speaker="Driver",
        main_subject="Driver",
        secondary_kind="none",
        secondary="",
        inset_context="",
        inset_query="",
        inset_contexts=(),
        moment_summary="",
        radio_lines=(),
        weather="none",
    )

    with patch.object(ig_mod, "SessionLocal", return_value=mock_db), \
         patch("app.services.ig_content_classifier.analyze_ig_post", return_value=analysis), \
         patch("app.tasks.ig_recreate._translate_quote", return_value="Translated Quote"), \
         patch("app.services.ai_caption.generate_caption", return_value=("Caption", "gemini")), \
         patch("app.tasks.ig_recreate.render_ig_recreate.apply_async") as mock_apply, \
         patch("app.tasks.ig_recreate.render_ig_recreate.delay") as mock_delay:

        # Case 1: source format chatgpt -> queues to visual queue
        recreate_post_for_fanpage(101, 1)
        assert mock_apply.called
        assert mock_apply.call_args[1].get("queue") == "visual"
        assert not mock_delay.called

        # Case 2: source format template -> uses delay() (default queue)
        link.visual_engine_override = "template"
        mock_apply.reset_mock()
        mock_delay.reset_mock()
        recreate_post_for_fanpage(101, 1)
        assert mock_delay.called
        assert not mock_apply.called

        # Case 3: source format repost (ig_recreate_enabled=False) -> skips
        link.ig_recreate_enabled = False
        link.visual_engine_override = None
        mock_apply.reset_mock()
        mock_delay.reset_mock()
        recreate_post_for_fanpage(101, 1)
        assert not mock_apply.called
        assert not mock_delay.called


def test_render_ig_recreate_engine_choice(tmp_path):
    """render_ig_recreate invokes redesign_card when source is chatgpt, skips when template."""
    import app.tasks.ig_recreate as ig_mod
    from app.tasks.ig_recreate import render_ig_recreate
    from app.models.publish_jobs import PublishJobStatus, ContentType
    from app.services.redesign_pipeline import RedesignResult

    img = tmp_path / "test.jpg"
    img.write_bytes(b"dummy")

    template_obj = SimpleNamespace(id=999, category="quote", template_json={"objects": []}, canvas_width=1080, canvas_height=1080)
    job = SimpleNamespace(
        id=77,
        post_id=101,
        fanpage_id=1,
        content_type=ContentType.ig_recreate,
        design_template_id=999,
        design_title="Title",
        design_subtitle=None,
        design_caption=None,
        design_analysis_json=None,
        design_not_before=None,
        status=PublishJobStatus.pending_design,
    )
    post = SimpleNamespace(
        id=101,
        ig_source_id=20,
        image_local_paths=[str(img)],
    )
    fp = SimpleNamespace(
        id=1,
        name="FP 1",
        visual_engine="chatgpt",  # Brand is ChatGPT, but source can override
        ig_recreate_smart_layout=False,
        design_expand=False,
        watermark_text="",
        accent_color=None,
        publish_mode="manual_review",
    )
    link = SimpleNamespace(
        fanpage_id=1,
        ig_source_id=20,
        ig_recreate_enabled=True,
        visual_engine_override="template",  # Source pins template
    )

    mock_db = MagicMock()
    mock_db.query.return_value.filter.return_value.update.return_value = 1
    mock_db.query.return_value.filter_by.side_effect = lambda **kw: (
        MagicMock(first=lambda: None) if "publish_job_id" in kw
        else MagicMock(first=lambda: job) if "id" in kw and kw["id"] == 77
        else MagicMock(first=lambda: fp) if "id" in kw and kw["id"] == 1
        else MagicMock(first=lambda: post) if "id" in kw and kw["id"] == 101
        else MagicMock(first=lambda: template_obj) if "id" in kw and kw["id"] == 999
        else MagicMock(first=lambda: SimpleNamespace(id=20, ig_username="racer_x")) if "id" in kw and kw["id"] == 20
        else MagicMock(first=lambda: link) if "fanpage_id" in kw and "ig_source_id" in kw
        else MagicMock(first=lambda: template_obj)
    )

    # When source override is template: redesign_card must NOT be called even though fp.visual_engine="chatgpt"
    with patch.object(ig_mod, "SessionLocal", return_value=mock_db), \
         patch("app.services.redesign_pipeline.redesign_card") as mock_redesign, \
         patch("app.services.design_images.resolve_template", return_value=SimpleNamespace(id=1, category="quote", template_json={}, canvas_width=1080, canvas_height=1080)), \
         patch("app.services.design_images.prepare_design_images", return_value=({}, ["data:image/jpeg;base64,abc"])), \
         patch("app.tasks.design_renderer.clear_template_text_objects", return_value={}), \
         patch("app.services.design_images.watermark_datauri", return_value=None), \
         patch("app.services.brand_look.brand_look", return_value=None), \
         patch("app.services.design_images.focus_points_for", return_value={}), \
         patch("app.services.design_images._safe_face_cy_ceiling", return_value=None), \
         patch("httpx.post", return_value=MagicMock(content=b"fake_png", raise_for_status=lambda: None)), \
         patch("pathlib.Path.mkdir", MagicMock()), \
         patch("pathlib.Path.write_bytes", MagicMock()):

        render_ig_recreate(77)
        assert not mock_redesign.called
        assert job.design_engine == "template"

    # When source override is chatgpt: redesign_card MUST be called
    link.visual_engine_override = "chatgpt"
    fp.visual_engine = "off"  # Brand is off, source is chatgpt
    success_redesign = RedesignResult(
        ok=True,
        layout="solo",
        image_path="/tmp/gen.png",
        image_url="/media/gen.png",
        final_text_rendered_by="template",
        notes=[],
        problems=[],
        content_type="quote",
    )
    with patch.object(ig_mod, "SessionLocal", return_value=mock_db), \
         patch("app.services.redesign_pipeline.redesign_card", return_value=success_redesign) as mock_redesign:

        render_ig_recreate(77)
        assert mock_redesign.called
        assert job.status == PublishJobStatus.pending_publish


def test_render_queue_for_source_override():
    """render_queue_for uses visual_engine_override for instagram content kind."""
    from app.services.engine_router import render_queue_for

    fp = SimpleNamespace(id=1, visual_engine="off")
    mock_db = MagicMock()
    with patch("app.services.engine_router.get_gptcli_config", return_value={"enabled": True}), \
         patch("app.services.engine_router.gptcli_applies", return_value=True):
        # Override chatgpt returns "visual"
        assert render_queue_for(mock_db, fp, "instagram", visual_engine_override="chatgpt") == "visual"
        # Override template returns None
        assert render_queue_for(mock_db, fp, "instagram", visual_engine_override="template") is None
        # Override None falls back to fp.visual_engine ("off" -> None)
        assert render_queue_for(mock_db, fp, "instagram", visual_engine_override=None) is None

