from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from app.models.design_templates import DesignTemplate
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.schemas.fanpage import FanpageBase, FanpageOut, FanpageUpdate
from app.tasks.design_renderer import _news_badge_text, render_discussion


def _make_job(**kwargs):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    defaults = {
        "attempt_count": 0,
        "created_at": now,
        "updated_at": now,
        "status": PublishJobStatus.pending_design,
    }
    defaults.update(kwargs)
    return PublishJob(**defaults)


def _setup_render_mock(db, job, fanpage, template=None):
    def mock_query(model):
        m = MagicMock()
        if model == PublishJob:
            m.filter.return_value.update.return_value = 1
            m.filter_by.return_value.first.return_value = job
        elif model == TargetFanpage:
            m.filter_by.return_value.first.return_value = fanpage
        elif model == DesignTemplate:
            m.filter_by.return_value.first.return_value = template
        else:
            m.filter_by.return_value.first.return_value = None
        return m

    db.query.side_effect = mock_query


# ── _news_badge_text tests ───────────────────────────────────────────


def test_news_badge_text_breaking_on():
    # Explicitly enabled
    fp = TargetFanpage(badge_breaking_enabled=True, mode2_badge_text="F1 INSIDER", mode2_gallery_niches=["F1"])
    assert _news_badge_text(fp, breaking=True) == "BREAKING"

    # Default / legacy fanpage without the attribute
    class LegacyFP:
        mode2_badge_text = "F1 INSIDER"
        mode2_gallery_niches = ["F1"]

    assert _news_badge_text(LegacyFP(), breaking=True) == "BREAKING"


def test_news_badge_text_breaking_off_mode2_badge_text():
    fp = TargetFanpage(
        badge_breaking_enabled=False,
        mode2_badge_text="SPECIAL REPORT",
        mode2_gallery_niches=["Motorsport"],
    )
    assert _news_badge_text(fp, breaking=True) == "SPECIAL REPORT"


def test_news_badge_text_breaking_off_niche_fallback():
    fp = TargetFanpage(
        badge_breaking_enabled=False,
        mode2_badge_text=None,
        mode2_gallery_niches=["MotoGP"],
    )
    assert _news_badge_text(fp, breaking=True) == "MotoGP NEWS"


def test_news_badge_text_breaking_off_empty():
    fp = TargetFanpage(
        badge_breaking_enabled=False,
        mode2_badge_text=None,
        mode2_gallery_niches=[],
    )
    assert _news_badge_text(fp, breaking=True) == ""


def test_news_badge_percent_100():
    fp = TargetFanpage(
        news_badge_percent=100,
        mode2_badge_text="F1 INSIDER",
    )
    # Both breaking and normal with job_id get badge
    assert _news_badge_text(fp, breaking=True, job_id=42) == "BREAKING"
    assert _news_badge_text(fp, breaking=False, job_id=42) == "F1 INSIDER"


def test_news_badge_percent_0():
    fp = TargetFanpage(
        news_badge_percent=0,
        mode2_badge_text="F1 INSIDER",
        badge_breaking_enabled=True,
    )
    # Percent 0 never shows badge, even when breaking
    assert _news_badge_text(fp, breaking=True, job_id=1) == ""
    assert _news_badge_text(fp, breaking=False, job_id=1) == ""
    assert _news_badge_text(fp, breaking=True, job_id=999) == ""


def test_news_badge_percent_50_stability_and_distribution():
    fp = TargetFanpage(
        news_badge_percent=50,
        mode2_badge_text="F1 NEWS",
    )
    # Stable: same job_id always gets same answer
    first_ans = _news_badge_text(fp, job_id=123)
    for _ in range(5):
        assert _news_badge_text(fp, job_id=123) == first_ans

    # Distribution over 200 ids: roughly half (between 35% and 65%, i.e. 70 to 130)
    badge_count = sum(1 for jid in range(1, 201) if _news_badge_text(fp, job_id=jid) == "F1 NEWS")
    assert 70 <= badge_count <= 130, f"Expected 35%-65% badges, got {badge_count}/200"


# ── render_discussion payload tests ──────────────────────────────────


@pytest.mark.parametrize(
    "design_subtitle,expected_label",
    [
        (None, "DISCUSSION"),
        ("HOT TAKE", "HOT TAKE"),
    ],
)
def test_render_discussion_payload_badge_on(tmp_path, monkeypatch, design_subtitle, expected_label):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=701,
        fanpage_id=1,
        content_type=ContentType.discussion,
        design_title="Who is the GOAT?",
        design_subtitle=design_subtitle,
        design_caption="F1 Drivers",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        badge_discussion_enabled=True,
        mode2_gallery_niches=["F1"],
        discussion_publish_mode=PublishMode.manual_review,
    )
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="discussion",
    )
    _setup_render_mock(db, job, fanpage, template=template)

    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.design_images.find_gallery_datauri", return_value=("data:image/png;base64,AAAA", MagicMock(id=1))), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp) as mock_post:
        render_discussion(701)

    assert mock_post.called
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["label"] == expected_label


def test_render_discussion_payload_badge_off(tmp_path, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "storage_base_path", str(tmp_path))

    db = MagicMock()
    job = _make_job(
        id=702,
        fanpage_id=1,
        content_type=ContentType.discussion,
        design_title="Who is the GOAT?",
        design_subtitle="HOT TAKE",
        design_caption="F1 Drivers",
    )
    fanpage = TargetFanpage(
        id=1,
        name="Test FP",
        badge_discussion_enabled=False,
        mode2_gallery_niches=["F1"],
        discussion_publish_mode=PublishMode.manual_review,
    )
    template = DesignTemplate(
        id=1,
        template_json={"objects": []},
        canvas_width=1080,
        canvas_height=1080,
        category="discussion",
    )
    _setup_render_mock(db, job, fanpage, template=template)

    mock_resp = MagicMock(status_code=200, content=b"png-output")
    mock_resp.raise_for_status = MagicMock()

    with patch("app.tasks.design_renderer.SessionLocal", return_value=db), \
         patch("app.services.design_images.resolve_template", return_value=template), \
         patch("app.services.design_images.find_gallery_datauri", return_value=("data:image/png;base64,AAAA", MagicMock(id=1))), \
         patch("app.services.design_images.prepare_design_images", return_value=({"objects": []}, ["data:image/png;base64,AAAA"])), \
         patch("app.tasks.design_renderer.httpx.post", return_value=mock_resp) as mock_post:
        render_discussion(702)

    assert mock_post.called
    sent_payload = mock_post.call_args.kwargs["json"]
    assert sent_payload["label"] == ""


# ── Schema round-trip tests ──────────────────────────────────────────


def test_fanpage_schema_badge_switches_defaults():
    base = FanpageBase()
    assert base.badge_breaking_enabled is True
    assert base.badge_discussion_enabled is True
    assert base.news_badge_percent == 100

    update = FanpageUpdate()
    assert update.badge_breaking_enabled is None
    assert update.badge_discussion_enabled is None
    assert update.news_badge_percent is None
    assert "badge_breaking_enabled" not in update.model_dump(exclude_unset=True)
    assert "badge_discussion_enabled" not in update.model_dump(exclude_unset=True)
    assert "news_badge_percent" not in update.model_dump(exclude_unset=True)


def test_fanpage_schema_badge_switches_roundtrip():
    data = {
        "badge_breaking_enabled": False,
        "badge_discussion_enabled": False,
        "news_badge_percent": 30,
    }
    base = FanpageBase(**data)
    assert base.badge_breaking_enabled is False
    assert base.badge_discussion_enabled is False
    assert base.news_badge_percent == 30

    update = FanpageUpdate(**data)
    dumped = update.model_dump(exclude_unset=True)
    assert dumped["badge_breaking_enabled"] is False
    assert dumped["badge_discussion_enabled"] is False
    assert dumped["news_badge_percent"] == 30

    now = datetime.now(timezone.utc)
    out = FanpageOut(
        **data,
        id=1,
        name="Test",
        platform_type="facebook",
        is_connected=True,
        created_at=now,
    )
    assert out.badge_breaking_enabled is False
    assert out.badge_discussion_enabled is False
    assert out.news_badge_percent == 30


def test_discussion_label_ignores_the_news_percentage():
    """A DISCUSSION label passed explicitly is not hidden by news_badge_percent; only by the discussion switch."""
    from types import SimpleNamespace
    from app.tasks.design_renderer import _discussion_badge_text, _FB_DISCUSSION_LABELS
    fp = SimpleNamespace(news_badge_percent=0, badge_discussion_enabled=True, badge_breaking_enabled=True)
    assert "DISCUSSION" in _FB_DISCUSSION_LABELS
    assert _discussion_badge_text(fp, "HOT TAKE") == "HOT TAKE"
    fp.badge_discussion_enabled = False
    assert _discussion_badge_text(fp, "HOT TAKE") == ""
