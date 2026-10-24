"""Tests for AI video list offset pagination, active-status filter, and counts endpoint.

Uses raw DDL to create only the minimal columns needed in SQLite — avoids
PostgreSQL ARRAY columns present in the real ORM models.
"""
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.ai_video import VideoIdea, VideoJob
from app.models.target_fanpages import TargetFanpage

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS target_fanpages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    repliz_account_id TEXT,
    name TEXT,
    username TEXT,
    picture_url TEXT,
    platform_type TEXT,
    is_connected INTEGER,
    timezone TEXT,
    target_country TEXT,
    is_active INTEGER,
    publish_mode TEXT,
    publish_sleep_start_hour INTEGER,
    publish_sleep_end_hour INTEGER,
    publish_daily_limit INTEGER,
    publish_hour_weights TEXT,
    mode1_ig_repost_enabled INTEGER,
    mode2_news_content_enabled INTEGER,
    mode2_publish_mode TEXT,
    mode2_gallery_keywords TEXT,
    mode2_gallery_niches TEXT,
    mode2_default_template_id INTEGER,
    default_quote_template_id INTEGER,
    default_news_template_id INTEGER,
    default_discussion_template_id INTEGER,
    accent_color TEXT,
    template_style TEXT,
    brand_override INTEGER,
    accent_secondary_color TEXT,
    badge_color TEXT,
    badge_text_color TEXT,
    quote_mark_color TEXT,
    quote_streak INTEGER,
    mode2_caption_tone TEXT,
    mode2_caption_language TEXT,
    mode2_caption_max_length INTEGER,
    mode2_caption_hashtag_count INTEGER,
    mode2_caption_cta_text TEXT,
    mode2_caption_custom_prompt TEXT,
    mode2_title_max_chars INTEGER,
    mode2_badge_text TEXT,
    mode2_source_attribution INTEGER,
    mode2_editorial_gate_enabled INTEGER,
    ig_recreate_enabled INTEGER,
    ig_recreate_quote_template_id INTEGER,
    ig_recreate_news_template_id INTEGER,
    ig_recreate_smart_layout INTEGER,
    ig_recreate_split_template_id INTEGER,
    design_expand INTEGER,
    discussion_enabled INTEGER,
    discussion_publish_mode TEXT,
    discussion_daily_count INTEGER,
    discussion_topic_mode TEXT,
    discussion_label_mode TEXT,
    pinterest_enabled INTEGER,
    pinterest_publish_mode TEXT,
    pinterest_daily_count INTEGER,
    pinterest_source_mode TEXT,
    pinterest_custom_prompt TEXT,
    pinterest_hashtag_count INTEGER,
    pinterest_allow_low_quality INTEGER,
    facebook_photo_enabled INTEGER,
    facebook_photo_publish_mode TEXT,
    facebook_photo_daily_count INTEGER,
    facebook_photo_topic_filter TEXT,
    yt_clip_enabled INTEGER,
    yt_clip_publish_mode TEXT,
    yt_clip_daily_count INTEGER,
    yt_clip_min_s INTEGER,
    yt_clip_max_s INTEGER,
    yt_clip_per_video INTEGER,
    yt_clip_min_score INTEGER,
    yt_clip_max_video_age_days INTEGER,
    yt_clip_captions INTEGER,
    yt_clip_watermark INTEGER,
    yt_clip_action_crop TEXT,
    yt_clip_title_max_chars INTEGER,
    yt_clip_description_max_chars INTEGER,
    ai_video_enabled INTEGER,
    ai_video_publish_mode TEXT,
    ai_video_daily_count INTEGER,
    ai_video_min_score INTEGER,
    ai_video_template TEXT,
    ai_video_templates TEXT,
    ai_video_clips INTEGER,
    ai_video_clips_min INTEGER,
    ai_video_seconds_min INTEGER,
    ai_video_seconds_max INTEGER,
    ai_video_max_credits_per_video INTEGER,
    ai_video_max_credits_per_day INTEGER,
    ai_video_narrator TEXT,
    ai_video_title_max_chars INTEGER,
    ai_video_description_max_chars INTEGER,
    ai_video_logo_enabled INTEGER,
    ai_video_logo_size_pct INTEGER,
    ai_video_logo_position TEXT,
    ai_video_logo_margin_pct INTEGER,
    ai_video_captions_enabled INTEGER,
    ai_video_caption_size INTEGER,
    ai_video_caption_bottom_pct INTEGER,
    ai_video_caption_highlight TEXT,
    ai_video_cover_enabled INTEGER,
    radar_enabled INTEGER,
    radar_niches TEXT,
    radar_shadow INTEGER,
    radar_min_likes INTEGER,
    radar_confirm_ratio REAL,
    radar_fast_ratio REAL,
    radar_fast_window_min INTEGER,
    radar_shelf_news_h INTEGER,
    radar_shelf_evergreen_h INTEGER,
    radar_burst_enabled INTEGER,
    radar_daily_max INTEGER,
    visual_engine TEXT,
    news_visual_engine TEXT,
    fb_visual_engine TEXT,
    quote_shorten_enabled INTEGER,
    qa_news_enabled INTEGER,
    qa_ig_enabled INTEGER,
    qa_facebook_enabled INTEGER,
    qa_ai_video_enabled INTEGER,
    ai_video_prompt_polish_enabled INTEGER,
    ai_video_clip_qc_enabled INTEGER,
    caption_tone TEXT,
    caption_language TEXT,
    caption_max_length INTEGER,
    caption_hashtag_count INTEGER,
    caption_must_include TEXT,
    caption_must_avoid TEXT,
    caption_cta_text TEXT,
    use_attribution INTEGER,
    caption_attribution_template TEXT,
    attribution_position TEXT,
    caption_custom_prompt TEXT,
    watermark_text TEXT,
    watermark_image_url TEXT,
    last_synced_at TEXT,
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS video_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fanpage_id INTEGER,
    template_key TEXT,
    topic TEXT,
    angle TEXT,
    research_notes TEXT,
    variables TEXT,
    clips INTEGER,
    score INTEGER,
    rationale TEXT,
    evidence TEXT,
    source TEXT,
    token_id INTEGER,
    status TEXT,
    error TEXT,
    created_at TEXT,
    decided_at TEXT,
    decided_by TEXT,
    client_ref TEXT,
    title TEXT,
    caption TEXT,
    prepared TEXT,
    prepared_at TEXT,
    polish_attempts INTEGER,
    polish_error TEXT
);

CREATE TABLE IF NOT EXISTS video_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER,
    fanpage_id INTEGER,
    template_key TEXT,
    title TEXT,
    caption TEXT,
    model TEXT,
    aspect TEXT,
    clip_seconds INTEGER,
    clips TEXT,
    credits_per_clip INTEGER,
    credits_estimate INTEGER,
    status TEXT,
    account_id INTEGER,
    worker_id TEXT,
    generation INTEGER,
    flow_project_id TEXT,
    clips_done TEXT,
    clip_attempts INTEGER,
    restarts INTEGER,
    unusual_hits INTEGER,
    lease_until TEXT,
    not_before TEXT,
    last_progress_at TEXT,
    video_path TEXT,
    video_url TEXT,
    thumbnail_path TEXT,
    thumbnail_url TEXT,
    cover_lines TEXT,
    cover_highlight TEXT,
    cover_prompt TEXT,
    cover_bg_path TEXT,
    duration_s REAL,
    qc TEXT,
    publish_job_id INTEGER,
    error TEXT,
    created_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    updated_at TEXT
);
"""


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def engine():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with eng.connect() as conn:
        for stmt in _CREATE_TABLES.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(text(stmt))
        conn.commit()
    return eng


@pytest.fixture(scope="module")
def Session(engine):
    return sessionmaker(bind=engine)


@pytest.fixture
def db(Session):
    session = Session()
    yield session
    session.rollback()
    session.close()


@pytest.fixture
def client(Session):
    session = Session()

    def override_db():
        return session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    c = TestClient(app)
    yield c
    app.dependency_overrides.pop(get_db, None)
    app.dependency_overrides.pop(get_current_user, None)
    session.rollback()
    session.close()


def _fanpage(session):
    uid = uuid4().hex
    fp = TargetFanpage(
        repliz_account_id=f"cnt-{uid}",
        name=f"FP {uid}",
        is_active=True,
        is_connected=True,
    )
    session.add(fp)
    session.flush()
    return fp


def _job(session, fanpage_id, status="queued"):
    j = VideoJob(
        fanpage_id=fanpage_id,
        template_key="motorsport_tech_explainer",
        title=f"Job {uuid4().hex[:6]}",
        status=status,
        generation=1,
        restarts=0,
    )
    session.add(j)
    session.flush()
    return j


def _idea(session, fanpage_id, status="proposed"):
    i = VideoIdea(
        fanpage_id=fanpage_id,
        template_key="motorsport_tech_explainer",
        topic=f"Topic {uuid4().hex[:6]}",
        status=status,
    )
    session.add(i)
    session.flush()
    return i


# ── jobs offset/pagination ───────────────────────────────────────────────────


def test_jobs_offset_pages_no_overlap(client, Session):
    """offset=0 and offset=N must return non-overlapping rows covering all rows."""
    session = Session()
    fp = _fanpage(session)
    job_ids = set()
    for _ in range(5):
        job_ids.add(_job(session, fp.id).id)
    session.commit()

    try:
        r1 = client.get("/ai-video/jobs", params={"fanpage_id": fp.id, "limit": 3, "offset": 0})
        assert r1.status_code == 200
        page1_ids = {j["id"] for j in r1.json()}

        r2 = client.get("/ai-video/jobs", params={"fanpage_id": fp.id, "limit": 3, "offset": 3})
        assert r2.status_code == 200
        page2_ids = {j["id"] for j in r2.json()}

        assert page1_ids & page2_ids == set()
        assert page1_ids | page2_ids == job_ids
    finally:
        session.query(VideoJob).filter(VideoJob.fanpage_id == fp.id).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_jobs_offset_all_rows_covered(client, Session):
    """Page-by-page with limit=2 covers every row exactly once."""
    session = Session()
    fp = _fanpage(session)
    all_ids = set()
    for _ in range(7):
        all_ids.add(_job(session, fp.id).id)
    session.commit()

    try:
        seen = set()
        offset = 0
        limit = 2
        while True:
            r = client.get("/ai-video/jobs", params={"fanpage_id": fp.id, "limit": limit, "offset": offset})
            assert r.status_code == 200
            page = r.json()
            if not page:
                break
            page_ids = {j["id"] for j in page}
            assert page_ids & seen == set(), "Overlapping ids between pages"
            seen |= page_ids
            offset += limit
        assert seen == all_ids
    finally:
        session.query(VideoJob).filter(VideoJob.fanpage_id == fp.id).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete(synchronize_session=False)
        session.commit()
        session.close()


# ── ideas active filter ──────────────────────────────────────────────────────


def test_ideas_status_active_returns_pilot_proposed_approved(client, Session):
    """status=active includes pilot/proposed/approved; excludes rejected/done/failed."""
    session = Session()
    fp = _fanpage(session)
    active_ids = set()
    active_ids.add(_idea(session, fp.id, "pilot").id)
    active_ids.add(_idea(session, fp.id, "proposed").id)
    active_ids.add(_idea(session, fp.id, "approved").id)
    excluded_ids = set()
    excluded_ids.add(_idea(session, fp.id, "rejected").id)
    excluded_ids.add(_idea(session, fp.id, "done").id)
    excluded_ids.add(_idea(session, fp.id, "failed").id)
    session.commit()

    try:
        r = client.get("/ai-video/ideas", params={"status": "active", "fanpage_id": fp.id})
        assert r.status_code == 200
        returned_ids = {i["id"] for i in r.json()}
        assert active_ids <= returned_ids
        assert returned_ids & excluded_ids == set()
    finally:
        session.query(VideoIdea).filter(VideoIdea.fanpage_id == fp.id).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_ideas_status_specific_still_works(client, Session):
    """status=proposed returns only proposed, not approved/pilot."""
    session = Session()
    fp = _fanpage(session)
    prop_id = _idea(session, fp.id, "proposed").id
    _idea(session, fp.id, "approved")
    session.commit()

    try:
        r = client.get("/ai-video/ideas", params={"status": "proposed", "fanpage_id": fp.id})
        assert r.status_code == 200
        returned_ids = {i["id"] for i in r.json()}
        assert prop_id in returned_ids
        for i in r.json():
            assert i["status"] == "proposed"
    finally:
        session.query(VideoIdea).filter(VideoIdea.fanpage_id == fp.id).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete(synchronize_session=False)
        session.commit()
        session.close()


# ── counts endpoint ──────────────────────────────────────────────────────────


def test_counts_jobs_total_matches_rows(client, Session):
    """counts.jobs_total equals actual job count; jobs_by_status is accurate."""
    session = Session()
    fp = _fanpage(session)
    for _ in range(4):
        _job(session, fp.id, "done")
    for _ in range(3):
        _job(session, fp.id, "failed")
    session.commit()

    try:
        r = client.get("/ai-video/counts", params={"fanpage_id": fp.id})
        assert r.status_code == 200
        data = r.json()
        assert data["jobs_total"] == 7
        assert data["jobs_by_status"]["done"] == 4
        assert data["jobs_by_status"]["failed"] == 3
    finally:
        session.query(VideoJob).filter(VideoJob.fanpage_id == fp.id).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_counts_ideas_active_sums_pilot_proposed_approved(client, Session):
    """counts.ideas_active == pilot+proposed+approved count."""
    session = Session()
    fp = _fanpage(session)
    _idea(session, fp.id, "pilot")
    _idea(session, fp.id, "proposed")
    _idea(session, fp.id, "approved")
    _idea(session, fp.id, "rejected")
    session.commit()

    try:
        r = client.get("/ai-video/counts", params={"fanpage_id": fp.id})
        assert r.status_code == 200
        data = r.json()
        assert data["ideas_active"] == 3
        assert data["ideas_by_status"]["rejected"] == 1
    finally:
        session.query(VideoIdea).filter(VideoIdea.fanpage_id == fp.id).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id == fp.id).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_counts_without_fanpage_id_returns_global(client, Session):
    """Without fanpage_id, counts include all fanpages."""
    session = Session()
    fp1 = _fanpage(session)
    fp2 = _fanpage(session)
    _job(session, fp1.id)
    _job(session, fp2.id)
    session.commit()

    try:
        r = client.get("/ai-video/counts")
        assert r.status_code == 200
        data = r.json()
        assert data["jobs_total"] >= 2
    finally:
        session.query(VideoJob).filter(VideoJob.fanpage_id.in_([fp1.id, fp2.id])).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id.in_([fp1.id, fp2.id])).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_counts_with_fanpage_id_scoped(client, Session):
    """With fanpage_id, counts only include that fanpage's rows."""
    session = Session()
    fp1 = _fanpage(session)
    fp2 = _fanpage(session)
    _job(session, fp1.id)
    _job(session, fp1.id)
    _job(session, fp2.id)
    session.commit()

    try:
        r = client.get("/ai-video/counts", params={"fanpage_id": fp1.id})
        assert r.status_code == 200
        data = r.json()
        assert data["jobs_total"] == 2
    finally:
        session.query(VideoJob).filter(VideoJob.fanpage_id.in_([fp1.id, fp2.id])).delete(synchronize_session=False)
        session.query(TargetFanpage).filter(TargetFanpage.id.in_([fp1.id, fp2.id])).delete(synchronize_session=False)
        session.commit()
        session.close()
