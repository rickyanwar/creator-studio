"""Group lanes: ig/news daily caps, brand allow-lists and brand template fallbacks."""

from datetime import datetime, timezone
import json
import sqlite3
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.models.design_templates import DesignTemplate
from app.models.fanpage_news_sources import FanpageNewsSource
from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareLane
from app.models.publish_jobs import PublishJob
from app.models.radar import RadarStoryDecision
from app.models.scraped_articles import ArticleStatus, ScrapedArticle
from app.models.settings import Settings
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.services.fb_share import group_jobs
from app.services.fb_share.brand_settings import set_brand_share

# Noon UTC = 07:00 in New York (the brand timezone); the New York day began at 05:00 UTC.
NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
BRAND, LANE, OTHER = 1, 2, 3
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)


@compiles(ARRAY, "sqlite")
@compiles(JSONB, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    with patch.object(SocialDestination.__table__.c.supported_media, "server_default", DefaultClause(text("'[]'"))):
        for model in (TargetFanpage, FbShareBrandSettings, FbShareGroupJob, FbShareLane, PublishJob,
                      SocialDestination, ScrapedArticle, FanpageNewsSource, DesignTemplate, Settings,
                      RadarStoryDecision):
            model.__table__.create(engine, checkfirst=True)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        session.add_all([
            TargetFanpage(id=BRAND, name="Brand", timezone="America/New_York",
                          is_active=True, is_connected=True, repliz_account_id="brand"),
            TargetFanpage(id=LANE, name="Brand (groups)", timezone="America/New_York", is_active=True,
                          mode1_ig_repost_enabled=True, mode2_news_content_enabled=True,
                          mode2_publish_mode=PublishMode.manual_review),
            TargetFanpage(id=OTHER, name="Other", is_active=True, is_connected=True, repliz_account_id="other",
                          mode1_ig_repost_enabled=True, mode2_news_content_enabled=True,
                          mode2_publish_mode=PublishMode.manual_review),
        ])
        session.add(FbShareLane(brand_fanpage_id=BRAND, lane_fanpage_id=LANE))
        session.flush()
        set_cap(session, ig=2, news=1)
        session.commit()
        yield session
    engine.dispose()


@pytest.fixture(autouse=True)
def no_real_redis(monkeypatch):
    from tests.test_fb_share_review_fixes2 import FakeRedis
    client = FakeRedis()
    monkeypatch.setattr(group_jobs, "redis_client", lambda: client)


def set_cap(db, *, ig=0, news=0, enabled=True):
    set_brand_share(db, BRAND, enabled, {"niches": ["f1"], "sources": {
        "ig": {"groups_only_per_day": ig}, "news": {"groups_only_per_day": news}}})
    db.flush()


def marker(db, mode, created_at, fanpage_id=BRAND):
    video_job_id = db.query(FbShareGroupJob).count() + 1000
    db.add(FbShareGroupJob(fanpage_id=fanpage_id, mode=mode, video_job_id=video_job_id,
                           created_at=created_at.replace(tzinfo=None)))
    db.flush()


# ── lane_daily_cap_reached ───────────────────────────────────────────────────

def test_cap_never_applies_to_a_normal_fanpage(db):
    set_cap(db, ig=0, news=0)
    assert not group_jobs.lane_daily_cap_reached(db, BRAND, "ig", NOW)
    assert not group_jobs.lane_daily_cap_reached(db, OTHER, "news", NOW)


def test_cap_counts_brand_markers_of_the_mode_in_the_brand_local_day(db):
    # New York day of NOW starts 2026-11-10 05:00 UTC.
    marker(db, "ig", datetime(2026, 11, 10, 4, 59, tzinfo=timezone.utc))   # yesterday in New York
    marker(db, "news", datetime(2026, 11, 10, 6, tzinfo=timezone.utc))     # other mode
    marker(db, "viral_radar", datetime(2026, 11, 10, 6, tzinfo=timezone.utc))
    marker(db, "ig", datetime(2026, 11, 10, 6, tzinfo=timezone.utc), fanpage_id=OTHER)  # other brand
    marker(db, "ig", datetime(2026, 11, 10, 5, 0, tzinfo=timezone.utc))
    assert not group_jobs.lane_daily_cap_reached(db, LANE, "ig", NOW)
    marker(db, "ig", datetime(2026, 11, 10, 11, tzinfo=timezone.utc))
    assert group_jobs.lane_daily_cap_reached(db, LANE, "ig", NOW)
    # The news cap (1) is already used by the news marker.
    assert group_jobs.lane_daily_cap_reached(db, LANE, "news", NOW)


def test_zero_cap_means_no_lane_posts_of_that_mode(db):
    set_cap(db, ig=0, news=3)
    assert group_jobs.lane_daily_cap_reached(db, LANE, "ig", NOW)
    assert not group_jobs.lane_daily_cap_reached(db, LANE, "news", NOW)


def test_only_ig_and_news_have_lane_caps(db):
    with pytest.raises(ValueError):
        group_jobs.lane_daily_cap_reached(db, LANE, "discussion", NOW)


def test_remaining_groups_only_today_is_gone():
    assert not hasattr(group_jobs, "remaining_groups_only_today")


# ── IG fan-out ───────────────────────────────────────────────────────────────

def link_for(db, fanpage_id, recreate=False):
    return SimpleNamespace(fanpage_id=fanpage_id, fanpage=db.get(TargetFanpage, fanpage_id),
                           ig_recreate_enabled=recreate)


@pytest.fixture
def fanout(monkeypatch):
    from app.tasks import fan_out
    caption = Mock()
    recreate = Mock()
    monkeypatch.setattr("app.tasks.ai_generator.generate_caption_for_job.apply_async", caption)
    monkeypatch.setattr("app.tasks.ig_recreate.recreate_post_for_fanpage.apply_async", recreate)
    # Markers get the real database clock, so fan-out runs on the real clock too.
    return SimpleNamespace(module=fan_out, caption=caption, recreate=recreate)


def test_lane_fanout_marks_ig_jobs_until_the_daily_cap(db, fanout):
    for post_id in (1, 2):
        assert fanout.module.fanout_link(db, SimpleNamespace(id=post_id), link_for(db, LANE), countdown=0)
    marks = db.query(FbShareGroupJob).order_by(FbShareGroupJob.id).all()
    assert [(m.fanpage_id, m.mode) for m in marks] == [(BRAND, "ig"), (BRAND, "ig")]
    # Third post today: over the lane cap (2) -> no job, no caption task.
    assert not fanout.module.fanout_link(db, SimpleNamespace(id=3), link_for(db, LANE), countdown=0)
    assert db.query(PublishJob).filter_by(fanpage_id=LANE).count() == 2
    assert fanout.caption.call_count == 2


def test_lane_fanout_over_cap_does_not_queue_a_recreate(db, fanout):
    set_cap(db, ig=0)
    assert not fanout.module.fanout_link(db, SimpleNamespace(id=1), link_for(db, LANE, recreate=True), countdown=0)
    fanout.recreate.assert_not_called()
    set_cap(db, ig=1)
    assert fanout.module.fanout_link(db, SimpleNamespace(id=1), link_for(db, LANE, recreate=True), countdown=0)
    fanout.recreate.assert_called_once()


def test_radar_dispatched_lane_fanout_uses_the_radar_cap_not_the_ig_cap(db, fanout):
    set_cap(db, ig=0)
    assert fanout.module.fanout_link(db, SimpleNamespace(id=1), link_for(db, LANE), countdown=0, radar_decision_id=99)


def test_page_fanout_is_unaffected_by_group_caps(db, fanout):
    set_cap(db, ig=0)
    assert fanout.module.fanout_link(db, SimpleNamespace(id=1), link_for(db, OTHER), countdown=0)
    assert db.query(PublishJob).filter_by(fanpage_id=OTHER).count() == 1
    assert db.query(FbShareGroupJob).count() == 0


def test_recreate_task_rechecks_the_lane_cap_before_any_ai_call(db, monkeypatch):
    from app.tasks import ig_recreate
    set_cap(db, ig=0)
    db.commit()
    monkeypatch.setattr(ig_recreate, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    analyze = Mock()
    monkeypatch.setattr("app.services.ig_content_classifier.analyze_ig_post", analyze)
    with patch.object(db, "query", wraps=db.query) as query:
        ig_recreate.recreate_post_for_fanpage.run(1, LANE)
    analyze.assert_not_called()
    queried = [call.args[0] for call in query.call_args_list]
    assert TargetFanpage not in queried  # returned before loading the post / fanpage
    assert db.query(PublishJob).count() == 0


# ── News copywriter ──────────────────────────────────────────────────────────

@pytest.fixture
def copywriter(db, monkeypatch):
    from app.tasks import news_copywriter
    db.add(ScrapedArticle(id=1, news_source_id=1, article_url="https://example.test/a", scraped_title="Title",
                          scraped_content="Body", status=ArticleStatus.scraped))
    for fanpage_id in (LANE, OTHER):
        db.add(FanpageNewsSource(fanpage_id=fanpage_id, news_source_id=1, is_active=True))
    db.commit()
    monkeypatch.setattr(news_copywriter, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    monkeypatch.setattr(news_copywriter, "_acquire_article_lock", lambda *a: True)
    monkeypatch.setattr(news_copywriter, "_release_article_lock", lambda *a: None)
    copy = Mock(return_value=SimpleNamespace(category="news", title="Headline", subtitle=None, caption="Caption",
                                             provider=None, is_breaking=False))
    monkeypatch.setattr("app.services.news_copywriter.generate_news_copy", copy)
    monkeypatch.setattr("app.services.news_dedup.find_duplicate_job", lambda *a: None)
    monkeypatch.setattr("app.services.design_images.resolve_template", lambda *a, **kw: None)
    monkeypatch.setattr("app.services.event_days.is_event_window", lambda *a: False)
    return SimpleNamespace(module=news_copywriter, copy=copy)


def test_lane_news_under_cap_is_a_marked_group_job(db, copywriter):
    copywriter.module.copywrite_article.run(1)
    lane_job = db.query(PublishJob).filter_by(fanpage_id=LANE).one()
    assert db.query(FbShareGroupJob).filter_by(publish_job_id=lane_job.id).one().mode == "news"
    assert db.query(PublishJob).filter_by(fanpage_id=OTHER).count() == 1


def test_lane_news_over_cap_skips_before_the_ai_call(db, copywriter):
    marker(db, "news", NOW.replace(hour=11))
    db.commit()
    with patch.object(copywriter.module, "datetime") as clock:
        clock.now.side_effect = lambda tz=None: NOW if tz else NOW.replace(tzinfo=None)
        copywriter.module.copywrite_article.run(1)
    assert db.query(PublishJob).filter_by(fanpage_id=LANE).count() == 0
    # The Page fanpage is unaffected and the article is finished.
    assert db.query(PublishJob).filter_by(fanpage_id=OTHER).count() == 1
    assert [call.args[0].id for call in copywriter.copy.call_args_list] == [OTHER]
    assert db.get(ScrapedArticle, 1).status == ArticleStatus.copywritten


@pytest.mark.parametrize("failure", ["copy", "dedup", "editorial"])
def test_news_releases_reservation_when_no_job_created(db, copywriter, monkeypatch, failure):
    client = group_jobs.redis_client()
    monkeypatch.setattr(copywriter.module, "_subscribed_fanpages", lambda *a: [db.get(TargetFanpage, LANE)])
    if failure == "copy":
        copywriter.copy.side_effect = RuntimeError("AI unavailable")
    elif failure == "dedup":
        monkeypatch.setattr("app.services.news_dedup.find_duplicate_job", lambda *a: SimpleNamespace(id=9))
    else:
        db.get(TargetFanpage, LANE).mode2_editorial_gate_enabled = True
        monkeypatch.setattr("app.services.editorial_gate.evaluate_article", lambda *a: SimpleNamespace(
            post_worthy=False, engagement=0, fact_check=True, fact_check_note="ok", reason="reject"))
    copywriter.module.copywrite_article.run(1)
    assert db.query(PublishJob).count() == 0
    assert client.reservations() == []


def test_job_creation_drops_the_reservation_the_marker_counts_instead(db, fanout, copywriter):
    client = group_jobs.redis_client()
    assert fanout.module.fanout_link(db, SimpleNamespace(id=1), link_for(db, LANE), countdown=0)
    copywriter.module.copywrite_article.run(1)
    assert client.reservations() == []
    assert sorted(m.mode for m in db.query(FbShareGroupJob).all()) == ["ig", "news"]


def test_recreate_releases_reservation_on_missing_post(db, monkeypatch):
    from app.tasks import ig_recreate
    from app.models.posts import Post
    Post.__table__.create(db.bind, checkfirst=True)
    monkeypatch.setattr(ig_recreate, "SessionLocal", lambda: db)
    monkeypatch.setattr(db, "close", lambda: None)
    ig_recreate.recreate_post_for_fanpage.run(99, LANE)
    assert group_jobs.redis_client().reservations() == []


# ── Allow-lists: a lane follows its brand ────────────────────────────────────

@pytest.mark.parametrize("listed,expected", [([BRAND], True), ([OTHER], False)])
def test_gptcli_allow_list_covers_the_lane_of_a_listed_brand(db, listed, expected):
    from app.services import gptcli_client
    db.add(Settings(gptcli_enabled=True, gptcli_token_encrypted="encrypted", gptcli_fanpage_ids=listed))
    db.commit()
    with patch.object(gptcli_client, "decrypt", return_value="token"):
        cfg = gptcli_client.get_gptcli_config(db)
    assert gptcli_client.gptcli_applies(cfg, LANE) is expected
    assert gptcli_client.gptcli_applies(cfg, listed[0]) is True
    # The settings page still shows only the brands the owner picked.
    assert cfg.fanpage_ids == tuple(listed)


@pytest.mark.parametrize("listed,expected", [([BRAND], True), ([OTHER], False), ([], True)])
def test_pro_poster_allow_list_covers_the_lane_of_a_listed_brand(db, listed, expected):
    from app.services.pro_poster.pipeline import pro_poster_applies
    db.add(Settings(pro_poster_enabled=True, pro_poster_fanpage_ids=listed))
    db.commit()
    assert pro_poster_applies(db, db.get(TargetFanpage, LANE)) is expected


# ── Template fallback: a lane renders with its brand's own templates ─────────

def test_resolve_template_uses_the_brand_templates_for_a_lane(db):
    from app.services.design_images import resolve_template
    db.add_all([
        DesignTemplate(id=10, name="Shared", category="news", is_default=True, fanpage_id=None, template_json={}),
        DesignTemplate(id=11, name="Brand news", category="news", is_default=True, fanpage_id=BRAND, template_json={}),
    ])
    db.commit()
    assert resolve_template(db, "news", fanpage=db.get(TargetFanpage, LANE)).id == 11
    assert resolve_template(db, "news", fanpage=db.get(TargetFanpage, OTHER)).id == 10
    assert resolve_template(db, "news", fanpage=db.get(TargetFanpage, BRAND)).id == 11
