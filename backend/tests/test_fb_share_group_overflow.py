"""Supply overflow regressions; also usable as a plugin to extend legacy SQLite schemas."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage, PublishMode
from app.models.social_publishing import PublishDelivery
from app.services.fb_share.group_jobs import is_group_only, mark_group_job
from app.services import visual_fair_queue as fair
from app.tasks import design_renderer as renderer
from backend.tests.test_visual_fair_queue import FakeRedis, _compile_array, _compile_jsonb


def pytest_configure(config):
    from sqlalchemy import event
    import app.services.visual_sourcing  # Load timezone data before legacy tests mock builtins.open.

    def create_group_tables(target, connection, **kwargs):
        for model in (FbShareBrandSettings, FbShareGroupJob):
            model.__table__.create(connection, checkfirst=True)

    event.listen(PublishJob.__table__, "after_create", create_group_tables)


@pytest.fixture(autouse=True)
def group_schema(request):
    # Legacy unit fixtures build selected tables instead of migrated metadata.
    if "db_session" in request.fixturenames:
        db = request.getfixturevalue("db_session")
        if isinstance(db, Session):
            for model in (FbShareBrandSettings, FbShareGroupJob):
                model.__table__.create(db.get_bind(), checkfirst=True)
    # Legacy renderer fakes only model their original queries; real-session tests exercise the mark.
    from app.services.fb_share import group_jobs
    original = group_jobs.is_group_only
    def check(db, job_id):
        return original(db, job_id) if isinstance(db, Session) else False
    original_remaining = group_jobs.remaining_groups_only_today
    def remaining(db, *args):
        return original_remaining(db, *args) if isinstance(db, Session) else 0
    with patch.object(group_jobs, "is_group_only", side_effect=check), \
         patch.object(group_jobs, "remaining_groups_only_today", side_effect=remaining):
        yield


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    from app.models.scraped_articles import ScrapedArticle
    from app.models.design_templates import DesignTemplate
    for model in (PublishJob, TargetFanpage, PublishDelivery, ScrapedArticle, DesignTemplate,
                  FbShareGroupJob, FbShareBrandSettings):
        model.__table__.create(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as db:
        yield db
    engine.dispose()


def news(db, *, age=1, breaking=False):
    fp = TargetFanpage(name="Page", mode2_publish_mode=PublishMode.auto)
    db.add(fp)
    db.flush()
    job = PublishJob(fanpage_id=fp.id, content_type=ContentType.news_content,
                     status=PublishJobStatus.pending_design, is_breaking=breaking,
                     created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=age))
    db.add(job)
    db.commit()
    return job


@pytest.mark.parametrize("quota", [0, 1])
def test_stale_news_overflow_renders(db_session, quota):
    job = news(db_session, age=25)
    with patch.object(renderer, "SessionLocal", return_value=db_session), \
         patch.object(renderer, "_acquire_queued_lock", return_value=True), \
         patch.object(renderer.render_design, "apply_async") as render, \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch("app.services.visual_fair_queue.render_queue_for", return_value="visual"), \
         patch("app.services.fb_share.group_jobs.remaining_groups_only_today", return_value=quota):
        renderer.render_pending_designs.run()
    assert is_group_only(db_session, job.id) == bool(quota)
    assert job.status == (PublishJobStatus.pending_design if quota else PublishJobStatus.skipped)
    assert render.call_count == quota


@pytest.mark.parametrize("quota", [0, 1])
@pytest.mark.parametrize("breaking,slot_none", [(True, True), (False, True), (False, False)])
def test_no_news_slot_overflow(db_session, quota, breaking, slot_none):
    job = news(db_session, breaking=breaking)
    now = datetime.now(timezone.utc)
    with patch.object(fair, "render_queue_for", return_value="visual"), \
         patch.object(fair, "_has_news_slot", return_value=False), \
         patch.object(fair, "_next_slot", return_value=None if slot_none else now), \
         patch.object(fair, "lookahead_horizon", return_value=None), \
         patch.object(fair, "remaining_groups_only_today", return_value=quota):
        assert fair.pick_next_news_job(db_session, FakeRedis(), now) is None
    assert is_group_only(db_session, job.id) == bool(quota)
    assert job.status == (PublishJobStatus.pending_design if quota else PublishJobStatus.skipped)


def test_group_news_bypasses_page_gates_and_counters(db_session):
    job = news(db_session, age=25)
    mark_group_job(db_session, fanpage_id=job.fanpage_id, mode="news", publish_job_id=job.id)
    db_session.commit()
    now = datetime.now(timezone.utc)
    with patch("app.services.social_delivery_planner.news_slot_available", side_effect=AssertionError), \
         patch("app.services.social_delivery_planner.next_news_slot_utc", side_effect=AssertionError):
        assert fair._has_news_slot(db_session, job, now)
        assert fair._next_slot(db_session, job, now) == now
    job.status = PublishJobStatus.pending_publish
    db_session.commit()
    assert fair.ahead_count(db_session, job.fanpage_id, now) == 0
    job.status = PublishJobStatus.pending_design
    db_session.commit()
    redis = FakeRedis()
    with patch.object(renderer, "SessionLocal", return_value=db_session), \
         patch.object(renderer, "_acquire_queued_lock", return_value=True), \
         patch.object(renderer.render_design, "apply_async") as render, \
         patch("app.services.engine_router.render_queue_for", return_value="visual"), \
         patch.object(fair, "render_queue_for", return_value="visual"), \
         patch.object(fair, "page_claimed", return_value=True), \
         patch.object(renderer, "_redis", return_value=redis):
        renderer.render_pending_designs.run()
    render.assert_called_once()
    assert fair.served_today(redis, [job.fanpage_id], now) == {job.fanpage_id: 0}


@pytest.mark.parametrize("quota", [0, 1])
def test_news_freshness_overflow(db_session, quota):
    job = news(db_session, age=3)
    with patch.object(fair, "render_queue_for", return_value="visual"), \
         patch("app.services.event_days.news_fresh_hours", return_value=2), \
         patch.object(fair, "remaining_groups_only_today", return_value=quota):
        assert fair.pick_next_news_job(db_session, FakeRedis(), datetime.now(timezone.utc)) is None
    assert is_group_only(db_session, job.id) == bool(quota)
    assert job.status == (PublishJobStatus.pending_design if quota else PublishJobStatus.skipped)


@pytest.mark.parametrize("quota", [0, 1])
def test_radar_daily_cap_overflow(quota):
    from app.models.radar import RadarStoryDecision, RadarStory, RadarPost, DecisionStatus
    from app.models.posts import Post
    from app.models.fanpage_sources import FanpageSource
    from app.models.ig_sources import IGSource
    from app.tasks.radar_dispatch import _process_story
    d = RadarStoryDecision(id=1, fanpage_id=1, story_id=1, status=DecisionStatus.QUEUED,
                           via_viral_only_link=True, rule="fast")
    member = RadarPost(id=1, shortcode="overflow", ig_username="source", caption="Caption")
    story = RadarStory(id=1, best_post_id=1, posts=[member])
    fp = TargetFanpage(id=1, radar_daily_max=1)
    source = IGSource(id=1, ig_username="source")
    link = FanpageSource(fanpage=fp, ig_source=source, trigger="viral_only")
    post = Post(id=1, image_local_paths=["source.jpg"])
    db = MagicMock()
    # Page decisions today (twice: stagger check, cap check), then pending group decisions (none yet).
    counts = iter([1, 1, 0])
    def query(model):
        q = MagicMock()
        if model is RadarStoryDecision:
            q.filter.return_value.all.return_value = [d]
        elif model is TargetFanpage:
            q.filter_by.return_value.first.return_value = fp
        elif model is FanpageSource:
            q.join.return_value.filter.return_value.all.return_value = [link]
        elif model is Post:
            q.filter.return_value.first.return_value = post
        elif model is PublishJob:
            q.join.return_value.filter.return_value.all.return_value = []
        else:
            q.filter.return_value.scalar.return_value = next(counts, 0) if "count" in str(model) else None
        return q
    db.query.side_effect = query
    with patch("app.tasks.radar_dispatch.remaining_groups_only_today", return_value=quota), \
         patch("app.tasks.radar_dispatch.recreate_post_for_fanpage.apply_async") as recreate:
        _process_story(db, story, "shared", 0, 10)
    assert d.status == (DecisionStatus.DISPATCHED if quota else DecisionStatus.SKIPPED_DAILY_MAX)
    assert recreate.call_count == quota
    if quota:
        assert recreate.call_args.kwargs["kwargs"] == {"radar_decision_id": 1, "group_only": True}


@pytest.mark.parametrize("quota", [0, 1])
def test_ig_completion_rescues_only_old_undelivered_jobs(db_session, quota):
    from app.models.posts import Post
    from app.models.ig_sources import IGSource
    from app.tasks import ai_generator
    for model in (Post, IGSource):
        model.__table__.create(db_session.get_bind())
    fp = TargetFanpage(name="IG Page", publish_mode=PublishMode.auto)
    source = IGSource(ig_username="source")
    db_session.add_all([fp, source])
    db_session.flush()
    post = Post(ig_source_id=source.id, ig_media_id="1", ig_post_url="https://example.test/1",
                media_type="image", original_caption="A real source caption")
    db_session.add(post)
    db_session.flush()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    current = PublishJob(fanpage_id=fp.id, post_id=post.id, status=PublishJobStatus.pending_caption)
    old = PublishJob(fanpage_id=fp.id, content_type=ContentType.ig_repost,
                     status=PublishJobStatus.pending_publish, created_at=now - timedelta(hours=25))
    delivered = PublishJob(fanpage_id=fp.id, content_type=ContentType.ig_repost,
                           status=PublishJobStatus.pending_publish, created_at=now - timedelta(hours=26))
    db_session.add_all([current, old, delivered])
    db_session.flush()
    # EXISTS must exclude jobs with any delivery, including failed/skipped attempts.
    db_session.execute(PublishDelivery.__table__.insert().values(
        publish_job_id=delivered.id, destination_id=1, state="skipped",
    ))
    db_session.commit()
    with patch.object(ai_generator, "SessionLocal", return_value=db_session), \
         patch("app.services.caption_grounding.has_usable_caption", return_value=True), \
         patch("app.services.ai_caption.generate_caption", return_value=("Caption", "groq")), \
         patch("app.services.ai_caption.build_caption_prompt", return_value="Prompt"), \
         patch("app.services.fb_share.group_jobs.remaining_groups_only_today", return_value=quota), \
         patch("app.tasks.publisher.publish_job.delay") as publish, \
         patch("app.tasks.publisher.publish_job.apply_async"), \
         patch.object(ai_generator, "_gap_since_last_publish", return_value=0):
        ai_generator.generate_caption_for_job.run(current.id)
    assert is_group_only(db_session, old.id) == bool(quota)
    assert not is_group_only(db_session, delivered.id)
    assert publish.call_count == quota


def test_ig_edit_group_completion_publishes_in_manual_mode():
    from app.tasks import ig_recreate
    db = MagicMock()
    db.query.return_value.filter_by.return_value.first.return_value = None
    job = SimpleNamespace(id=1, fanpage_id=1, design_analysis_json={})
    fp = SimpleNamespace(id=1, publish_mode=PublishMode.manual_review)
    with patch("app.services.ig_photo_edit.edit_ig_photo", return_value={
        "ok": True, "image_path": "/media/designs/group.jpg", "engine": "chatgpt",
    }), patch("app.services.qa_toggles.qa_enabled", return_value=True), \
         patch("app.services.fb_share.group_jobs.is_group_only", return_value=True), \
         patch("app.tasks.publisher.publish_job.delay") as publish:
        ig_recreate._render_ig_edit(db, job, SimpleNamespace(image_local_paths=["original.jpg"]),
                                   fp, SimpleNamespace(chatgpt_edit_prompt="Keep it real"))
    assert job.status == PublishJobStatus.pending_publish
    publish.assert_called_once_with(job.id)


@pytest.mark.parametrize("quota", [0, 1])
def test_radar_recreate_marks_created_job_before_render(quota):
    from dataclasses import dataclass
    from unittest.mock import mock_open
    from app.tasks import ig_recreate
    from app.models.posts import Post
    from app.models.ig_sources import IGSource
    from app.models.fanpage_sources import FanpageSource
    from app.models.radar import RadarStoryDecision, DecisionStatus

    @dataclass
    class Analysis:
        type: str = "news"
        text: str = "Source headline"

    db = MagicMock()
    decision = SimpleNamespace(id=3, story=None, rule="fast", status=DecisionStatus.DISPATCHED)
    fp = SimpleNamespace(id=1, name="Page", ig_recreate_enabled=True, radar_niches=[], mode2_gallery_niches=[])
    post = SimpleNamespace(id=2, ig_source_id=1, image_local_paths=["source.jpg"], original_caption="Source")
    rows = {Post: post, TargetFanpage: fp, IGSource: SimpleNamespace(niche="sports"),
            RadarStoryDecision: decision, FanpageSource: None}
    def query(model):
        q = MagicMock()
        q.filter_by.return_value.first.return_value = rows.get(model)
        return q
    db.query.side_effect = query
    def flush():
        for call in db.add.call_args_list:
            if isinstance(call.args[0], PublishJob):
                call.args[0].id = 7
    db.flush.side_effect = flush
    with patch.object(ig_recreate, "SessionLocal", return_value=db), \
         patch("builtins.open", mock_open(read_data=b"photo")), \
         patch("app.services.ig_mode.resolve_effective_engine", return_value="chatgpt"), \
         patch("app.services.ig_content_classifier.analyze_ig_post", return_value=Analysis()), \
         patch.object(ig_recreate, "_recreate_niche", return_value="sports"), \
         patch.object(ig_recreate, "_rewrite_news_title", return_value="Headline"), \
         patch("app.services.design_images.resolve_template", return_value=SimpleNamespace(id=1)), \
         patch("app.services.ai_caption.build_caption_prompt", return_value="Prompt"), \
         patch("app.services.ai_caption.generate_caption", return_value=("Caption", "groq")), \
         patch("app.services.fb_share.group_jobs.remaining_groups_only_today", return_value=quota), \
         patch("app.services.fb_share.group_jobs.mark_group_job") as mark, \
         patch.object(ig_recreate.render_ig_recreate, "apply_async") as render:
        ig_recreate.recreate_post_for_fanpage.run(2, 1, radar_decision_id=3, group_only=True)
    assert mark.call_count == quota
    assert render.call_count == quota
    if quota:
        mark.assert_called_once_with(db, fanpage_id=1, mode="viral_radar", publish_job_id=7)
        assert decision.publish_job_id == 7
    else:
        assert decision.status == DecisionStatus.SKIPPED_DAILY_MAX
