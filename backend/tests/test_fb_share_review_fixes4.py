"""REVIEW2 fixes: edit-once provenance + Group Share gate, self-healing lane reservations,
guarded reuse copies, old raw file cleanup and single-quote verbatim checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from app.models.publish_jobs import ContentType, PublishJobStatus
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services import ai_image_reuse
from app.services.fb_share import group_jobs
from app.services.fb_share.brand_settings import set_brand_share
from app.services.fb_share.classify import _quotes_verbatim
from app.services.pro_poster.pipeline import _remember_poster, _reuse_sibling_poster, compose_pro_poster
from tests.test_ai_image_reuse import (  # noqa: F401  (fixtures)
    PROMPT, _finished_sibling, _job, _poster_sibling, _render_ig_edit, db, lane, storage,
)
from tests.test_fb_share_review_fixes2 import FakeRedis

ARTICLE = SimpleNamespace(id=7)
SISTER_ID = 2


def _sister_on_same_profile(db, **look):
    db.add(TargetFanpage(id=SISTER_ID, name="Sister", **look))
    db.add_all([
        SocialDestination(id=10, content_project_id=1, name="Profile", kind="facebook_profile"),
        SocialDestination(id=11, content_project_id=SISTER_ID, name="Sister Page", kind="facebook_page",
                          parent_profile_id=10),
    ])
    db.commit()
    return db.get(TargetFanpage, SISTER_ID)


def _lane_poster_copy(db, lane):
    """The lane renders article 7 by copying the brand's Pro poster."""
    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)
    with patch("app.services.pro_poster.pipeline.generate_ex", side_effect=AssertionError("engine called")), \
         patch("app.services.pro_poster.pipeline.qa_enabled", return_value=True):
        compose_pro_poster(db, lane, lane_job, ARTICLE)
    lane_job.status = PublishJobStatus.pending_publish
    db.commit()
    return lane_job


def _reedit(db, job):
    from app.api.publish_jobs import reedit_job

    with patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.delay") as render, \
         patch("app.api.publish_jobs._enrich_job", return_value=None):
        reedit_job(job.id, db, None)
    render.assert_called_once_with(job.id)


def _find(db, job, **key):
    key = {"source_key": "news:7", "format_key": "pro_poster", "prompt_text": "", **key}
    return ai_image_reuse.find_reusable(db, job, **key)


# ── 1. Provenance ────────────────────────────────────────────────────────────


def test_copy_records_the_origin_job_and_file(db, storage, lane):
    brand_job, path = _poster_sibling(db, storage, 1)
    lane_job = _lane_poster_copy(db, lane)

    record = lane_job.design_analysis_json["ai_image_reuse"]
    assert record["origin_job_id"] == brand_job.id and record["origin_path"] == str(path)
    assert brand_job.design_analysis_json["ai_image_reuse"]["origin_job_id"] == brand_job.id


def test_reedit_never_takes_the_rejected_image_back_from_the_lane_copy(db, storage, lane):
    brand_job, _ = _poster_sibling(db, storage, 1, status=PublishJobStatus.published)
    _lane_poster_copy(db, lane)

    _reedit(db, brand_job)

    assert brand_job.status == PublishJobStatus.pending_design
    analysis = brand_job.design_analysis_json
    assert "ai_image_reuse" not in analysis and "pro_poster_reused_from" not in analysis
    with patch("app.services.pro_poster.pipeline.qa_enabled", return_value=False):
        assert _reuse_sibling_poster(db, db.get(TargetFanpage, 1), brand_job, ARTICLE) is None


def test_reedit_of_a_copy_rejects_its_origin_image_too(db, storage, lane):
    lane_job, lane_path = _poster_sibling(db, storage, lane.id)
    brand_job = _job(db, 1, ContentType.news_content, source_article_id=7)
    with patch("app.services.pro_poster.pipeline.generate_ex", side_effect=AssertionError("engine called")), \
         patch("app.services.pro_poster.pipeline.qa_enabled", return_value=True):
        compose_pro_poster(db, db.get(TargetFanpage, 1), brand_job, ARTICLE)
    brand_job.status = PublishJobStatus.published
    db.commit()
    own_copy = Path(brand_job.design_analysis_json["ai_image_reuse"]["path"])
    assert own_copy.is_file()

    _reedit(db, brand_job)

    assert not own_copy.exists()  # the job's own raw copy goes with the record
    assert brand_job.design_analysis_json["ai_image_reuse_rejected"] == [str(lane_path)]
    assert _find(db, brand_job) is None
    assert lane_path.is_file()  # the lane's own file is never touched


def test_deleted_skipped_failed_or_reset_origin_invalidates_every_copy(db, storage, lane):
    brand_job, _ = _poster_sibling(db, storage, 1)
    lane_copy = _lane_poster_copy(db, lane)
    sister = _sister_on_same_profile(db)
    sister_job = _job(db, sister.id, ContentType.news_content, source_article_id=7)
    found = _find(db, sister_job)
    assert found.job_id == lane_copy.id and found.origin_job_id == brand_job.id

    for change in ({"is_deleted": True}, {"status": PublishJobStatus.skipped},
                   {"status": PublishJobStatus.failed}, {"status": PublishJobStatus.pending_design}):
        saved = {name: getattr(brand_job, name) for name in change}
        for name, value in change.items():
            setattr(brand_job, name, value)
        db.commit()
        assert _find(db, sister_job) is None, change
        for name, value in saved.items():
            setattr(brand_job, name, value)
        db.commit()
    assert _find(db, sister_job) is not None


def test_origin_that_made_a_new_image_invalidates_old_copies(db, storage, lane):
    brand_job, _ = _poster_sibling(db, storage, 1)
    _lane_poster_copy(db, lane)
    sister = _sister_on_same_profile(db)
    sister_job = _job(db, sister.id, ContentType.news_content, source_article_id=7)
    new_path = storage / "poster-v2.png"
    new_path.write_bytes(b"NEW")
    ai_image_reuse.remember(brand_job, str(new_path), source_key="news:7", format_key="pro_poster",
                            prompt_text="", engine="gptcli", qa_checked=True)
    brand_job.status = PublishJobStatus.published
    db.commit()

    found = _find(db, sister_job)

    assert found is None  # the brand job itself is not a sibling of the sister Page; the lane copy is stale


def test_render_now_drops_the_reuse_record(db, storage, lane):
    from app.api.publish_jobs import render_now

    brand_job, _ = _poster_sibling(db, storage, 1, status=PublishJobStatus.pending_design)
    with patch("app.services.engine_router.render_queue_for", return_value=None), \
         patch("app.tasks.design_renderer.render_design.delay"), \
         patch("app.api.publish_jobs._enrich_job", return_value=None):
        render_now(brand_job.id, db, None)
    db.refresh(brand_job)
    assert "ai_image_reuse" not in (brand_job.design_analysis_json or {})


# ── 2. Group Share gate ──────────────────────────────────────────────────────


def test_group_share_disabled_means_no_reuse_and_no_raw_file(db, storage, lane):
    _poster_sibling(db, storage, 1)
    set_brand_share(db, 1, False, {})
    db.commit()
    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)
    brand_job = _job(db, 1, ContentType.news_content, source_article_id=8)

    assert _find(db, lane_job) is None
    assert not ai_image_reuse.has_siblings(db, 1) and not ai_image_reuse.has_siblings(db, lane.id)
    _remember_poster(db, db.get(TargetFanpage, 1), brand_job, SimpleNamespace(id=8), b"\x89PNGposter",
                     engine="gptcli", qa_checked=True, layout="hero_solo")
    assert not (brand_job.design_analysis_json or {}).get("ai_image_reuse")
    assert not list(storage.glob(f"designs/job_{brand_job.id}_*"))


def test_brand_and_its_lane_reuse_without_a_look_check(db, storage, lane):
    lane.accent_color = "#000000"  # out of sync on purpose; same brand needs no look match
    db.commit()
    brand_job, _ = _poster_sibling(db, storage, 1)
    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)

    assert _find(db, lane_job).job_id == brand_job.id


@pytest.mark.parametrize("field,value", [
    ("accent_color", "#FF0000"), ("accent_secondary_color", "#00FF00"),
    ("template_style", "classic"), ("caption_language", "id"),
])
def test_cross_brand_lane_reuse_needs_the_same_look(db, storage, lane, field, value):
    sister = _sister_on_same_profile(db)
    sister_job, _ = _poster_sibling(db, storage, sister.id)
    lane_job = _job(db, lane.id, ContentType.news_content, source_article_id=7)
    assert _find(db, lane_job).job_id == sister_job.id  # same look (defaults) -> reuse

    setattr(sister, field, value)
    db.commit()

    assert _find(db, lane_job) is None


def test_page_to_page_never_reuses_even_with_group_share_on(db, storage, lane):
    sister = _sister_on_same_profile(db)
    set_brand_share(db, SISTER_ID, True, {})
    db.commit()
    _poster_sibling(db, storage, 1)
    sister_job = _job(db, sister.id, ContentType.news_content, source_article_id=7)

    assert ai_image_reuse.reuse_sibling_ids(db, SISTER_ID) == {lane.id}
    assert _find(db, sister_job) is None


# ── 4. Guards and old files ──────────────────────────────────────────────────


def test_ig_edit_renders_normally_when_the_reuse_copy_fails(db, storage, lane):
    _finished_sibling(db, storage, 1, source_key="ig:55", format_key="ig_edit", post_id=55)
    lane_job = _job(db, lane.id, post_id=55)

    with patch.object(ai_image_reuse, "copy_reused", side_effect=OSError("disk full")):
        edit = _render_ig_edit(db, lane_job, lane)

    edit.assert_called_once()
    assert lane_job.design_image_path == "/media/designs/new.jpg"
    assert "reused" not in lane_job.design_engine_note
    assert lane_job.design_analysis_json["ai_image_reuse"]["origin_job_id"] == lane_job.id


def test_facebook_edit_renders_normally_when_the_reuse_copy_fails(db, storage, lane):
    from app.tasks import design_renderer
    from tests.test_ai_image_reuse import _fb_marker

    _finished_sibling(db, storage, 1, source_key="fb:987", format_key="fb_edit",
                      content_type=ContentType.facebook_recreate, source_gallery_image_id=_fb_marker(db, 1, "987"))
    lane_job = _job(db, lane.id, ContentType.facebook_recreate, source_gallery_image_id=_fb_marker(db, lane.id, "987"))
    idea = SimpleNamespace(image_local_path="/nowhere/fb.jpg", design_subtitle=None)
    edited = {"ok": True, "image_path": "/media/designs/fb-new.jpg", "engine": "chatgpt", "reason": ""}

    with patch.object(ai_image_reuse, "copy_reused", side_effect=OSError("gone")), \
         patch("app.services.ig_photo_edit.edit_ig_photo", return_value=edited) as edit, \
         patch("app.services.qa_toggles.qa_enabled", return_value=True), \
         patch("app.tasks.publisher.publish_job.delay"):
        design_renderer._render_facebook_direct(db, lane_job, lane, idea, prompt=PROMPT)

    edit.assert_called_once()
    assert lane_job.design_image_path == "/media/designs/fb-new.jpg"


def test_new_record_deletes_the_previous_own_raw_file(db, storage, lane):
    job = _job(db, 1, ContentType.news_content, source_article_id=7)
    first = ai_image_reuse.save_ai_image(job, b"\x89PNGfirst")
    key = dict(source_key="news:7", format_key="pro_poster", prompt_text="", engine="gptcli", qa_checked=True)
    ai_image_reuse.remember(job, first, **key)
    second = ai_image_reuse.save_ai_image(job, b"\x89PNGsecond")

    ai_image_reuse.remember(job, second, **key)

    assert not Path(first).exists() and Path(second).is_file()
    ai_image_reuse.remember(job, second, **key)  # same path again: kept
    assert Path(second).is_file()


def test_new_record_never_deletes_the_design_or_a_foreign_file(db, storage, lane):
    job = _job(db, 1, ContentType.ig_recreate, post_id=55)
    design = ai_image_reuse.save_ai_image(job, b"\x89PNGdesign")
    job.design_image_path = design
    key = dict(source_key="ig:55", format_key="ig_edit", prompt_text=PROMPT, engine="chatgpt", qa_checked=True)
    ai_image_reuse.remember(job, design, **key)
    foreign = storage / "designs" / "job_999_ai_source_x.png"
    foreign.write_bytes(b"x")

    ai_image_reuse.remember(job, str(foreign), **key)
    ai_image_reuse.remember(job, ai_image_reuse.save_ai_image(job, b"\x89PNGnew"), **key)

    assert Path(design).is_file() and foreign.is_file()


# ── 3. Self-healing lane reservations ────────────────────────────────────────


@pytest.fixture
def reservations(monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(group_jobs, "redis_client", lambda: redis)
    monkeypatch.setattr("app.services.fb_share.lanes.lane_brand_id", lambda db, fid: 1 if fid == 2 else None)
    monkeypatch.setattr(group_jobs, "get_brand_share",
                        lambda *a: (True, {"sources": {"ig": {"groups_only_per_day": 1}}}))
    db = Mock()
    db.get.return_value = SimpleNamespace(timezone="UTC")
    db.query.return_value.filter.return_value.count.return_value = 0
    return SimpleNamespace(redis=redis, db=db, now=datetime(2026, 11, 10, 12, tzinfo=timezone.utc))


def test_concurrent_reservations_with_cap_one_have_one_winner(reservations):
    with ThreadPoolExecutor(max_workers=6) as pool:
        tokens = list(pool.map(
            lambda _: group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now), range(6)))
    assert sum(isinstance(token, str) for token in tokens) == 1
    assert tokens.count(None) == 5
    assert len(reservations.redis.reservations()) == 1


def test_expired_reservation_frees_the_slot(reservations):
    token = group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now)
    assert isinstance(token, str)
    assert group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now) is None

    # The worker died before creating the job: the key expires, the slot is free again.
    reservations.redis.now += group_jobs.LANE_RESERVATION_TTL_SECONDS
    assert isinstance(group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now), str)


def test_markers_and_live_reservations_both_count(reservations, monkeypatch):
    monkeypatch.setattr(group_jobs, "get_brand_share",
                        lambda *a: (True, {"sources": {"ig": {"groups_only_per_day": 2}}}))
    reservations.db.query.return_value.filter.return_value.count.return_value = 1
    assert isinstance(group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now), str)
    assert group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now) is None


def test_busy_lock_refuses_and_releases_nothing_foreign(reservations, monkeypatch):
    monkeypatch.setattr(group_jobs, "LANE_LOCK_WAIT_SECONDS", 0)
    lock = f"{group_jobs.LANE_LOCK_PREFIX}1:ig:2026-11-10"
    reservations.redis.set(lock, "other-worker", px=group_jobs.LANE_LOCK_TTL_MS)

    assert group_jobs.reserve_lane_slot(reservations.db, 2, "ig", reservations.now) is None
    assert reservations.redis.get(lock) == "other-worker"


def test_release_ignores_non_reservation_tokens(reservations):
    reservations.redis.set("other:key", 1)
    for token in (None, False, "other:key"):
        group_jobs.release_lane_slot(token)
    assert reservations.redis.get("other:key") == 1


# ── 4. Verbatim quotes in single quotes and guillemets ───────────────────────

SOURCE = "“I’m not done yet,” said Márquez. He added 'we go again' and «Forza»."


@pytest.mark.parametrize("caption,ok", [
    ("Márquez: ‘I am not finished’", False),
    ("Márquez: ‘I’m not done yet,’", True),
    ("Márquez: 'we go slower'", False),
    ("Márquez: 'we go again'", True),
    ("«Forza Ducati»", False),
    ("«Forza»", True),
    ("Don't miss Márquez's comeback, the drivers' title is open", True),
    ("Don’t miss Márquez’s comeback", True),
])
def test_single_quotes_and_guillemets_must_be_verbatim(caption, ok):
    assert _quotes_verbatim(caption, SOURCE) is ok
