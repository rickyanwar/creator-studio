from __future__ import annotations

from datetime import datetime, date, time, timedelta, timezone
import json
import sqlite3
import zoneinfo
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.fanpage_news_sources import FanpageNewsSource
from app.models.news_sources import NewsSource
from app.models.posts import Post
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ScrapedArticle
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.scripts.target_gap_report import (
    build_report,
    categorize_news_error,
    format_report_text,
    main,
)


@pytest.fixture
def db_session():
    """Create in-memory SQLite database for testing."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    sd_cp_col = SocialDestination.__table__.columns["content_project_id"]
    orig_sd_cp = sd_cp_col.nullable
    sd_cp_col.nullable = True

    pj_fp_col = PublishJob.__table__.columns["fanpage_id"]
    orig_pj_fp = pj_fp_col.nullable
    pj_fp_col.nullable = True

    engine = create_engine("sqlite:///:memory:")
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    NewsSource.__table__.create(engine)
    FanpageNewsSource.__table__.create(engine)
    ScrapedArticle.__table__.create(engine)

    sd_col.server_default = orig_sd
    sd_cp_col.nullable = orig_sd_cp
    pj_fp_col.nullable = orig_pj_fp

    session = Session(engine)
    try:
        yield session
    finally:
        session.close()


def _create_fixture_fanpage(
    db: Session,
    fanpage_id: int = 1,
    name: str = "Test Brand",
    is_active: bool = True,
    editorial_gate_enabled: bool = False,
    news_content_enabled: bool = True,
    ai_video_daily_count: int | None = None,
) -> TargetFanpage:
    fp = TargetFanpage(
        id=fanpage_id,
        name=name,
        target_country="GB",
        timezone="Europe/London",
        is_active=is_active,
        mode1_ig_repost_enabled=True,
        mode2_news_content_enabled=news_content_enabled,
        mode2_editorial_gate_enabled=editorial_gate_enabled,
        ai_video_daily_count=ai_video_daily_count,
    )
    db.add(fp)
    db.flush()
    return fp


def _create_fixture_destination(
    db: Session,
    dest_id: int = 1,
    fanpage_id: int = 1,
    daily_target: int = 10,
    timezone_str: str = "Europe/London",
    mode_shares: dict | None = None,
    is_active: bool = True,
) -> SocialDestination:
    dest = SocialDestination(
        id=dest_id,
        content_project_id=fanpage_id,
        kind="facebook_page",
        name=f"Dest {dest_id}",
        daily_target=daily_target,
        timezone=timezone_str,
        mode_shares=mode_shares,
        is_active=is_active,
    )
    db.add(dest)
    db.flush()
    return dest


# ── Case 1: Target 10, 10 published deliveries -> AT_TARGET ──────────────────


def test_case_1_at_target(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, fanpage_id=fp.id, daily_target=10, timezone_str="UTC")

    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    for i in range(10):
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=i + 1,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        )
        db_session.add(deliv)
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    day_rep = dest_rep["days"][0]

    assert day_rep["posted"] == 10
    assert day_rep["target"] == 10
    assert day_rep["parts"] == {}

    text = format_report_text(report)
    assert "AT_TARGET" in text


# ── Case 2: Target 10, 2 published + 5 failed deliveries -> DELIVERY_FAILED ──


def test_case_2_delivery_failed(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, fanpage_id=fp.id, daily_target=10, timezone_str="UTC")

    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    for i in range(2):
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=i + 1,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        )
        db_session.add(deliv)

    for i in range(5):
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=i + 10,
            state="failed",
            last_error="Repliz: Missing or invalid image file",
            scheduled_for=datetime.combine(day, time(11, i)),
            content_type_snapshot="news_content",
        )
        db_session.add(deliv)
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    day_rep = dest_rep["days"][0]

    assert day_rep["posted"] == 2
    assert day_rep["failed"] == 5
    assert day_rep["parts"] == {"DELIVERY_FAILED": 5, "NO_SUPPLY": 3}
    assert sum(day_rep["parts"].values()) == 8

    text = format_report_text(report)
    assert "DELIVERY_FAILED 5" in text
    assert "Repliz: Missing or invalid image file" in text


# ── Case 3: Mode shares, news posted, ig 0, news skipped -> QUOTA_BLOCKED ────


def test_case_3_quota_blocked(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=10,
        timezone_str="UTC",
        mode_shares={"ig": 30, "news": 70},
    )

    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # 7 news posted
    for i in range(7):
        job = PublishJob(
            id=i + 1,
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        )
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        )
        db_session.add_all([job, deliv])

    # 4 news skipped with stale error
    for i in range(4):
        job = PublishJob(
            id=i + 10,
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.skipped,
            last_error="stale: news older than 12 h, not rendered",
            created_at=datetime.combine(day, time(11, i)),
        )
        db_session.add(job)
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    day_rep = dest_rep["days"][0]

    assert day_rep["posted"] == 7
    assert day_rep["parts"] == {"QUOTA_BLOCKED": 3}
    assert sum(day_rep["parts"].values()) == 3

    text = format_report_text(report)
    assert "QUOTA_BLOCKED 3" in text
    assert "ig" in text
    assert "3" in text


# ── Case 4: Target 10, only 4 jobs created that day, 4 posted -> NO_SUPPLY ───


def test_case_4_no_supply(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, fanpage_id=fp.id, daily_target=10, timezone_str="UTC")

    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # Link one active news source and one inactive news source
    s1 = NewsSource(id=1, name="Active Source", category_url="https://a.com", is_active=True)
    s2 = NewsSource(id=2, name="Inactive Source", category_url="https://b.com", is_active=False)
    db_session.add_all([s1, s2])
    db_session.flush()

    link1 = FanpageNewsSource(fanpage_id=fp.id, news_source_id=s1.id, is_active=True)
    link2 = FanpageNewsSource(fanpage_id=fp.id, news_source_id=s2.id, is_active=True)
    db_session.add_all([link1, link2])
    db_session.flush()

    for i in range(4):
        job = PublishJob(
            id=i + 1,
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        )
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        )
        db_session.add_all([job, deliv])
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    day_rep = dest_rep["days"][0]

    assert day_rep["posted"] == 4
    assert day_rep["parts"] == {"NO_SUPPLY": 6}
    assert sum(day_rep["parts"].values()) == 6

    text = format_report_text(report)
    assert "NO_SUPPLY 6" in text
    assert "jobs created 4/10" in text
    assert "Inactive Source" in text


# ── Case 5: Event multiplier monkeypatch to 2 -> target doubles ───────────────


def test_case_5_event_multiplier(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, fanpage_id=fp.id, daily_target=5, timezone_str="UTC")

    import app.scripts.target_gap_report as tgr
    monkeypatch.setattr(tgr, "event_multiplier", lambda db, fanpage, day: 2)

    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    day_rep = dest_rep["days"][0]

    assert day_rep["multiplier"] == 2
    assert day_rep["target"] == 10


# ── Case 6: Timezone boundaries (23:30 local belongs to that local day) ──────


def test_case_6_timezone_boundaries(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(
        db_session, fanpage_id=fp.id, daily_target=10, timezone_str="America/New_York"
    )

    # 2026-10-25 is EDT (UTC-4).
    # 23:30 local on 2026-10-25 is 2026-10-26 03:30 UTC.
    deliv = PublishDelivery(
        destination_id=dest.id,
        publish_job_id=1,
        state="published",
        scheduled_for=datetime(2026, 10, 26, 3, 30, 0),
        content_type_snapshot="news_content",
    )
    db_session.add(deliv)
    db_session.flush()

    # Query 2 complete days up to 2026-10-27 12:00 EDT (2026-10-27 16:00 UTC)
    # The complete days are 2026-10-25 and 2026-10-26.
    now_utc = datetime(2026, 10, 27, 16, 0, 0, tzinfo=timezone.utc)
    report = build_report(db_session, days=2, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]

    days_by_date = {d["date"]: d for d in dest_rep["days"]}
    assert "2026-10-25" in days_by_date
    assert "2026-10-26" in days_by_date

    # Belongs to 2026-10-25, not 2026-10-26
    assert days_by_date["2026-10-25"]["posted"] == 1
    assert days_by_date["2026-10-26"]["posted"] == 0


# ── Case 7: --json path returns valid JSON ────────────────────────────────────


def test_case_7_json_output(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, fanpage_id=fp.id, daily_target=10, timezone_str="UTC")

    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)

    json_str = json.dumps(report)
    parsed = json.loads(json_str)

    assert "destinations" in parsed
    assert "top_causes" in parsed
    assert len(parsed["destinations"]) == 1
    assert parsed["destinations"][0]["destination_id"] == dest.id
    assert "parts" in parsed["destinations"][0]["days"][0]


# ── Extra tests: edge cases and helpers ───────────────────────────────────────


def test_categorize_news_error_all_categories():
    assert categorize_news_error("stale: news older than 12 h") == "stale_age"
    assert categorize_news_error("article superseded by breaking news") == "superseded"
    assert categorize_news_error("no news slot left today") == "no_slot"
    assert categorize_news_error("too old to post at dispatch time") == "dispatch_old"
    assert categorize_news_error("Repliz: missing or invalid image file") == "repliz_image"
    assert categorize_news_error("needs manual image review") == "no_photo"
    assert categorize_news_error("failed: no distinct photo found") == "no_photo"
    assert categorize_news_error("phone identity verification error") == "phone_identity"
    assert categorize_news_error("random network timeout") == "other"
    assert categorize_news_error(None) == "other"


def test_cooccurring_delivery_failed_and_quota_blocked(db_session: Session):
    """Shortfall splits into multiple parts: DELIVERY_FAILED, QUOTA_BLOCKED, NO_SUPPLY."""
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=10,
        timezone_str="UTC",
        mode_shares={"ig": 30, "news": 70},
    )
    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # 1 published, 4 failed -> shortfall = 9. DELIVERY_FAILED = 4, r1 = 5.
    deliv = PublishDelivery(
        destination_id=dest.id, publish_job_id=1, state="published",
        scheduled_for=datetime.combine(day, time(10, 0)), content_type_snapshot="news_content",
    )
    db_session.add(deliv)
    for i in range(4):
        db_session.add(PublishDelivery(
            destination_id=dest.id, publish_job_id=i + 2, state="failed",
            last_error="Repliz: network timeout", scheduled_for=datetime.combine(day, time(11, i)),
            content_type_snapshot="news_content",
        ))

    # News jobs skipped stale -> QUOTA_BLOCKED condition holds because ig (quota 3) has 0 posted
    for i in range(3):
        db_session.add(PublishJob(
            id=i + 10, fanpage_id=fp.id, content_type=ContentType.news_content,
            status=PublishJobStatus.skipped, last_error="news older than 12 h",
            created_at=datetime.combine(day, time(9, i)),
        ))
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    day_rep = report["destinations"][0]["days"][0]

    assert day_rep["parts"] == {"DELIVERY_FAILED": 4, "QUOTA_BLOCKED": 3, "NO_SUPPLY": 2}
    assert sum(day_rep["parts"].values()) == 9
    text = format_report_text(report)
    assert "DELIVERY_FAILED 4, QUOTA_BLOCKED 3, NO_SUPPLY 2" in text


# ── TASK-F2 Required Tests ──────────────────────────────────────────────────


def test_gp_weekend_like_day(db_session: Session):
    """1. GP-Weekend-like day: target 29, posted 16, 0 failed, mode_shares, 1 stale -> QUOTA_BLOCKED 1, NO_SUPPLY 12."""
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=29,
        timezone_str="UTC",
        mode_shares={"ig": 4, "news": 86, "ai_video": 10},
    )
    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # 16 news jobs created: 15 published, 1 skipped stale
    for i in range(15):
        db_session.add(PublishJob(
            id=i + 1,
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        ))
    db_session.add(PublishJob(
        id=16,
        fanpage_id=fp.id,
        content_type=ContentType.news_content,
        status=PublishJobStatus.skipped,
        last_error="stale: news older than 12 h, not rendered",
        created_at=datetime.combine(day, time(9, 15)),
    ))

    # 16 deliveries posted (all news)
    for i in range(16):
        db_session.add(PublishDelivery(
            destination_id=dest.id,
            publish_job_id=i + 1,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        ))
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    day_rep = report["destinations"][0]["days"][0]

    assert day_rep["posted"] == 16
    assert day_rep["target"] == 29
    assert day_rep["parts"] == {"QUOTA_BLOCKED": 1, "NO_SUPPLY": 12}
    assert sum(day_rep["parts"].values()) == 13

    text = format_report_text(report)
    assert "- QUOTA_BLOCKED 1, NO_SUPPLY 12" in text


def test_fanpage_inactive(db_session: Session):
    """2. Destination active, fan page inactive, 0 jobs, target 27 -> FANPAGE_INACTIVE 27."""
    fp = _create_fixture_fanpage(db_session, is_active=False)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=27,
        timezone_str="UTC",
        is_active=True,
    )
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)
    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    day_rep = report["destinations"][0]["days"][0]

    assert day_rep["posted"] == 0
    assert day_rep["target"] == 27
    assert day_rep["parts"] == {"FANPAGE_INACTIVE": 27}
    assert sum(day_rep["parts"].values()) == 27
    text = format_report_text(report)
    assert "FANPAGE_INACTIVE 27" in text


def test_ai_video_cap(db_session: Session):
    """3. Fan page with ai_video_daily_count 3, target 10, 3 ai_video posted, only ai_video jobs created -> AI_VIDEO_CAP 7."""
    fp = _create_fixture_fanpage(db_session, ai_video_daily_count=3)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=10,
        timezone_str="UTC",
    )
    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    for i in range(3):
        job = PublishJob(
            id=i + 1,
            fanpage_id=fp.id,
            content_type=ContentType.ai_video,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        )
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="ai_video",
        )
        db_session.add_all([job, deliv])
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    day_rep = report["destinations"][0]["days"][0]

    assert day_rep["posted"] == 3
    assert day_rep["target"] == 10
    assert day_rep["parts"] == {"AI_VIDEO_CAP": 7}
    assert sum(day_rep["parts"].values()) == 7
    text = format_report_text(report)
    assert "AI_VIDEO_CAP 7" in text
    assert "ai_video cap 3/day" in text


def test_day_with_failed_deliveries_and_no_supply(db_session: Session):
    """4. Day with 9 failed deliveries, target 29, posted 2, 12 jobs created -> DELIVERY_FAILED 9; parts sum to 27."""
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=29,
        timezone_str="UTC",
    )
    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    for i in range(2):
        db_session.add(PublishDelivery(
            destination_id=dest.id,
            publish_job_id=i + 1,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        ))
    for i in range(9):
        db_session.add(PublishDelivery(
            destination_id=dest.id,
            publish_job_id=i + 10,
            state="failed",
            last_error="Repliz: network timeout",
            scheduled_for=datetime.combine(day, time(11, i)),
            content_type_snapshot="news_content",
        ))
    for i in range(12):
        db_session.add(PublishJob(
            id=i + 1,
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        ))
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    day_rep = report["destinations"][0]["days"][0]

    assert day_rep["posted"] == 2
    assert day_rep["target"] == 29
    assert day_rep["parts"]["DELIVERY_FAILED"] == 9
    assert sum(day_rep["parts"].values()) == 27
    text = format_report_text(report)
    assert "DELIVERY_FAILED 9" in text


def test_no_supply_editorial_gate_and_articles_scraped(db_session: Session):
    """5. NO_SUPPLY detail contains editorial gate ON and articles scraped when the fan page has the gate on."""
    fp = _create_fixture_fanpage(db_session, editorial_gate_enabled=True)
    dest = _create_fixture_destination(
        db_session,
        fanpage_id=fp.id,
        daily_target=10,
        timezone_str="UTC",
    )
    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    s1 = NewsSource(id=1, name="Source 1", category_url="https://a.com", is_active=True)
    db_session.add(s1)
    db_session.add(FanpageNewsSource(fanpage_id=fp.id, news_source_id=s1.id, is_active=True))
    art = ScrapedArticle(
        id=1,
        news_source_id=s1.id,
        article_url="https://a.com/article1",
        scraped_title="News Title",
        scraped_content="News Content",
        scraped_at=datetime.combine(day, time(8, 0)),
    )
    db_session.add(art)

    for i in range(2):
        job = PublishJob(
            id=i + 1,
            fanpage_id=fp.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        )
        deliv = PublishDelivery(
            destination_id=dest.id,
            publish_job_id=job.id,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        )
        db_session.add_all([job, deliv])
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    day_rep = report["destinations"][0]["days"][0]

    assert day_rep["parts"]["NO_SUPPLY"] == 8
    text = format_report_text(report)
    assert "editorial gate ON" in text
    assert "articles scraped" in text
    assert "NO_SUPPLY 8" in text


def test_top_causes_sums_parts_across_destinations(db_session: Session):
    """6. Top causes sums parts across two destinations."""
    fp1 = _create_fixture_fanpage(db_session, fanpage_id=1, name="FP 1")
    fp2 = _create_fixture_fanpage(db_session, fanpage_id=2, name="FP 2")
    dest1 = _create_fixture_destination(
        db_session,
        dest_id=1,
        fanpage_id=fp1.id,
        daily_target=10,
        timezone_str="UTC",
    )
    dest2 = _create_fixture_destination(
        db_session,
        dest_id=2,
        fanpage_id=fp2.id,
        daily_target=10,
        timezone_str="UTC",
    )
    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    # Dest 1: 5 failed deliveries -> DELIVERY_FAILED 5, NO_SUPPLY 5
    for i in range(5):
        db_session.add(PublishDelivery(
            destination_id=dest1.id,
            publish_job_id=i + 1,
            state="failed",
            last_error="Fail",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        ))

    # Dest 2: 4 posted, 4 jobs created -> NO_SUPPLY 6
    for i in range(4):
        job = PublishJob(
            id=i + 10,
            fanpage_id=fp2.id,
            content_type=ContentType.news_content,
            status=PublishJobStatus.published,
            created_at=datetime.combine(day, time(9, i)),
        )
        deliv = PublishDelivery(
            destination_id=dest2.id,
            publish_job_id=job.id,
            state="published",
            scheduled_for=datetime.combine(day, time(10, i)),
            content_type_snapshot="news_content",
        )
        db_session.add_all([job, deliv])
    db_session.flush()

    report = build_report(db_session, days=1, now_utc=now_utc)
    top_causes = {tc["cause"]: tc["shortfall"] for tc in report["top_causes"]}

    assert top_causes["NO_SUPPLY"] == 11
    assert top_causes["DELIVERY_FAILED"] == 5


def test_inactive_destination_skipped(db_session: Session):
    fp = _create_fixture_fanpage(db_session)
    _create_fixture_destination(db_session, dest_id=1, fanpage_id=fp.id, is_active=False)
    _create_fixture_destination(db_session, dest_id=2, fanpage_id=fp.id, is_active=True)

    report = build_report(db_session, days=1)
    assert len(report["destinations"]) == 1
    assert report["destinations"][0]["destination_id"] == 2


def test_main_cli_execution(db_session: Session, monkeypatch: pytest.MonkeyPatch, capsys):
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, dest_id=1, fanpage_id=fp.id, daily_target=10, is_active=True)

    import app.scripts.target_gap_report as tgr

    class DummySession:
        def __init__(self, s):
            self._s = s
        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._s, name)

    monkeypatch.setattr(tgr, "SessionLocal", lambda: DummySession(db_session))

    # Test JSON output
    rc = main(["--json", "--destination", str(dest.id), "--days", "1"])
    assert rc == 0
    captured = capsys.readouterr()
    parsed = json.loads(captured.out)
    assert "destinations" in parsed

    # Test text output
    rc = main(["--destination", str(dest.id), "--days", "1"])
    assert rc == 0
    captured = capsys.readouterr()
    assert f"Dest {dest.id}" in captured.out


def test_target_bounded_by_fitting_daily_target(db_session: Session, monkeypatch: pytest.MonkeyPatch):
    """Target in analyze_day and analyze_today_so_far is bounded by fitting_daily_target."""
    fp = _create_fixture_fanpage(db_session)
    dest = _create_fixture_destination(db_session, fanpage_id=fp.id, daily_target=10, timezone_str="UTC")
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    import app.scripts.target_gap_report as tgr
    monkeypatch.setattr(tgr, "fitting_daily_target", lambda target, gap, s_start, s_end: 4)

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    assert dest_rep["days"][0]["target"] == 4
    assert dest_rep["today_so_far"]["target"] == 4


def test_destination_no_fanpage_ignores_orphan_jobs(db_session: Session):
    """When destination has no fanpage, jobs query returns 0 even if fanpage_id IS NULL jobs exist."""
    dest = SocialDestination(
        id=99,
        content_project_id=None,
        name="No Fanpage Dest",
        kind="facebook_page",
        mode="repliz",
        timezone="UTC",
        daily_target=5,
        min_gap_minutes=5,
        is_active=True,
    )
    db_session.add(dest)

    day = date(2026, 10, 28)
    now_utc = datetime(2026, 10, 29, 12, 0, 0, tzinfo=timezone.utc)

    orphan_job = PublishJob(
        fanpage_id=None,
        content_type=ContentType.news_content,
        created_at=datetime.combine(day, time(10, 0)),
        is_deleted=False,
    )
    db_session.add(orphan_job)
    db_session.flush()

    report = build_report(db_session, days=1, destination_id=dest.id, now_utc=now_utc)
    dest_rep = report["destinations"][0]
    day_rep = dest_rep["days"][0]
    assert day_rep["details"]["supply"]["jobs_created"] == 0
    assert day_rep["jobs_created"] == {}


def test_report_notes_present(db_session: Session):
    """Report includes race-day notes in JSON and text output."""
    report = build_report(db_session, days=1)
    expected_note = "Note: race-day x2 for past days uses the current event dates; a race weekend that already ended may show as 1x."
    assert "notes" in report
    assert expected_note in report["notes"]

    text = format_report_text(report)
    assert expected_note in text

