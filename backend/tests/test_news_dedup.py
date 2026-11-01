import os
if not os.environ.get("DATABASE_URL") or "db:5432" in os.environ.get("DATABASE_URL", ""):
    os.environ["DATABASE_URL"] = "postgresql://postgres:admin@localhost:5432/test_news_dedup"

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest

from app.database import Base, SessionLocal, engine
from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.scraped_articles import ArticleStatus, ScrapedArticle
from app.models.target_fanpages import PublishMode, TargetFanpage
from app.models.news_sources import NewsSource
from app.services.news_dedup import (
    DEDUP_WINDOW_HOURS,
    MAX_CANDIDATES,
    build_prompt,
    find_duplicate_job,
    parse_verdict,
    recent_story_jobs,
)
from app.tasks.news_copywriter import copywrite_article


# ── Test 1: parse_verdict ───────────────────────────────────────────────────

def test_parse_verdict():
    # {"duplicate_of": 2} with 3 candidates -> 2
    assert parse_verdict('{"duplicate_of": 2}', 3) == 2
    # "2" string -> 2
    assert parse_verdict('{"duplicate_of": "2"}', 3) == 2
    # null -> None
    assert parse_verdict('{"duplicate_of": null}', 3) is None
    # 0 -> None
    assert parse_verdict('{"duplicate_of": 0}', 3) is None
    # 4 -> None
    assert parse_verdict('{"duplicate_of": 4}', 3) is None
    # "abc" -> None
    assert parse_verdict('{"duplicate_of": "abc"}', 3) is None
    # text wrapped in ```json fences -> parsed
    assert parse_verdict('```json\n{"duplicate_of": 2}\n```', 3) == 2


# ── Test 2: build_prompt ────────────────────────────────────────────────────

def test_build_prompt():
    art = SimpleNamespace(
        scraped_title="New Headline Special",
        scraped_content="X" * 1000,
    )
    cand1 = SimpleNamespace(
        scraped_title="Earlier Story 1",
        scraped_content="A" * 300,
    )
    cand2 = SimpleNamespace(
        scraped_title="Earlier Story 2",
        scraped_content="B" * 50 + "\n" + "C" * 50,
    )
    prompt = build_prompt("MotoGP", art, [cand1, cand2])

    assert "New Headline Special" in prompt
    assert "Earlier Story 1" in prompt
    assert "Earlier Story 2" in prompt
    assert "1. Earlier Story 1 — " in prompt
    assert "2. Earlier Story 2 — " in prompt
    assert "MotoGP" in prompt
    assert "If you are not sure, answer null." in prompt

    # candidate content cut to 200 chars
    assert "A" * 200 in prompt
    assert "A" * 201 not in prompt

    # new article content cut to 600 chars
    assert "X" * 600 in prompt
    assert "X" * 601 not in prompt

    # candidate content newlines replaced by spaces
    assert "B" * 50 + " " + "C" * 50 in prompt


# ── Test 3: find_duplicate_job with recent_story_jobs monkeypatched ─────────

def test_find_duplicate_job_no_candidates(monkeypatch):
    monkeypatch.setattr("app.services.news_dedup.recent_story_jobs", lambda *args, **kwargs: [])
    ai_called = False

    def fake_caption(prompt):
        nonlocal ai_called
        ai_called = True
        return ('{"duplicate_of": 1, "reason": "ok"}', "provider")

    monkeypatch.setattr("app.services.news_dedup.generate_caption", fake_caption)

    fp = SimpleNamespace(id=1, name="FP", mode2_gallery_niches=["MotoGP"])
    art = SimpleNamespace(id=10, scraped_title="Title", scraped_content="Content")
    res = find_duplicate_job(None, fp, art)
    assert res is None
    assert not ai_called


def test_find_duplicate_job_match(monkeypatch):
    job1 = SimpleNamespace(id=101)
    job2 = SimpleNamespace(id=102)
    art1 = SimpleNamespace(id=11, scraped_title="Cand 1", scraped_content="Content 1")
    art2 = SimpleNamespace(id=12, scraped_title="Cand 2", scraped_content="Content 2")
    candidates = [(job1, art1), (job2, art2)]

    monkeypatch.setattr("app.services.news_dedup.recent_story_jobs", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(
        "app.services.news_dedup.generate_caption",
        lambda prompt: ('{"duplicate_of": 2, "reason": "same incident"}', "provider"),
    )

    fp = SimpleNamespace(id=1, name="FP", mode2_gallery_niches=["MotoGP"])
    art = SimpleNamespace(id=10, scraped_title="Title", scraped_content="Content")
    res = find_duplicate_job(None, fp, art)
    assert res is job2


def test_find_duplicate_job_ai_replies_null(monkeypatch):
    job1 = SimpleNamespace(id=101)
    art1 = SimpleNamespace(id=11, scraped_title="Cand 1", scraped_content="Content 1")
    candidates = [(job1, art1)]

    monkeypatch.setattr("app.services.news_dedup.recent_story_jobs", lambda *args, **kwargs: candidates)
    monkeypatch.setattr(
        "app.services.news_dedup.generate_caption",
        lambda prompt: ('{"duplicate_of": null, "reason": "different stories"}', "provider"),
    )

    fp = SimpleNamespace(id=1, name="FP", mode2_gallery_niches=["MotoGP"])
    art = SimpleNamespace(id=10, scraped_title="Title", scraped_content="Content")
    res = find_duplicate_job(None, fp, art)
    assert res is None


def test_find_duplicate_job_ai_raises(monkeypatch):
    job1 = SimpleNamespace(id=101)
    art1 = SimpleNamespace(id=11, scraped_title="Cand 1", scraped_content="Content 1")
    candidates = [(job1, art1)]

    monkeypatch.setattr("app.services.news_dedup.recent_story_jobs", lambda *args, **kwargs: candidates)

    def raise_err(prompt):
        raise RuntimeError("AI service down")

    monkeypatch.setattr("app.services.news_dedup.generate_caption", raise_err)

    fp = SimpleNamespace(id=1, name="FP", mode2_gallery_niches=["MotoGP"])
    art = SimpleNamespace(id=10, scraped_title="Title", scraped_content="Content")
    with pytest.raises(RuntimeError, match="AI service down"):
        find_duplicate_job(None, fp, art)


# ── Test 4: copywrite_article wiring ────────────────────────────────────────

def test_copywrite_article_wiring_skips_when_duplicate_found():
    mock_article = MagicMock()
    mock_article.id = 10
    mock_article.status = ArticleStatus.scraped
    mock_article.news_source_id = 100

    mock_fp = MagicMock()
    mock_fp.id = 1
    mock_fp.mode2_editorial_gate_enabled = False
    mock_fp.mode2_publish_mode = PublishMode.auto

    fake_job = MagicMock()
    fake_job.id = 999

    with patch("app.tasks.news_copywriter.SessionLocal") as mock_session_cls, \
         patch("app.tasks.news_copywriter._subscribed_fanpages", return_value=[mock_fp]), \
         patch("app.services.news_copywriter.generate_news_copy") as mock_gen_copy, \
         patch("app.services.news_dedup.find_duplicate_job", return_value=fake_job) as mock_find_dup:
        db = MagicMock()
        mock_session_cls.return_value = db
        db.query().filter_by().first.side_effect = [mock_article, None]

        copywrite_article(10)

        assert mock_find_dup.called
        assert not mock_gen_copy.called
        assert not db.add.called


def test_copywrite_article_wiring_proceeds_when_find_duplicate_raises():
    mock_article = MagicMock()
    mock_article.id = 10
    mock_article.status = ArticleStatus.scraped
    mock_article.news_source_id = 100

    mock_fp = MagicMock()
    mock_fp.id = 1
    mock_fp.mode2_editorial_gate_enabled = False
    mock_fp.mode2_publish_mode = PublishMode.auto

    mock_copy = MagicMock()
    mock_copy.title = "Title"
    mock_copy.subtitle = "Sub"
    mock_copy.caption = "Cap"
    mock_copy.provider = "gemini"
    mock_copy.is_breaking = False
    mock_copy.category = "news"

    with patch("app.tasks.news_copywriter.SessionLocal") as mock_session_cls, \
         patch("app.tasks.news_copywriter._subscribed_fanpages", return_value=[mock_fp]), \
         patch("app.services.news_copywriter.generate_news_copy", return_value=mock_copy) as mock_gen_copy, \
         patch("app.services.design_images.resolve_template", return_value=None), \
         patch("app.tasks.design_renderer.render_design.apply_async"), \
         patch("app.services.news_dedup.find_duplicate_job", side_effect=RuntimeError("flaky AI")):
        db = MagicMock()
        mock_session_cls.return_value = db
        db.query().filter_by().first.side_effect = [mock_article, None]

        copywrite_article(10)

        assert mock_gen_copy.called
        assert db.add.called


# ── DB test: recent_story_jobs ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def setup_postgres():
    try:
        with engine.connect() as conn:
            pass
        Base.metadata.create_all(bind=engine)
    except Exception as exc:
        pytest.skip(f"Local Postgres not reachable: {exc}")
    yield


@pytest.fixture
def db_session(setup_postgres):
    session = SessionLocal()
    try:
        session.query(PublishJob).delete()
        session.query(ScrapedArticle).delete()
        session.query(NewsSource).delete()
        session.query(TargetFanpage).delete()
        session.commit()
        yield session
    finally:
        session.rollback()
        session.close()


def test_recent_story_jobs_db(db_session):
    now = datetime(2026, 11, 2, 12, 0, 0)
    fp1 = TargetFanpage(id=1, name="FP1", repliz_account_id="r1", publish_mode=PublishMode.auto)
    fp2 = TargetFanpage(id=2, name="FP2", repliz_account_id="r2", publish_mode=PublishMode.auto)
    ns = NewsSource(id=1, name="NS1", category_url="http://ns1", is_active=True)
    db_session.add_all([fp1, fp2, ns])
    db_session.commit()

    # Target article (id=10)
    art10 = ScrapedArticle(
        id=10, news_source_id=1, article_url="http://a10",
        scraped_title="Target", scraped_content="Target content",
        status=ArticleStatus.scraped,
    )
    # Valid candidates
    art12 = ScrapedArticle(
        id=12, news_source_id=1, article_url="http://a12",
        scraped_title="Candidate 1", scraped_content="Content 1",
        status=ArticleStatus.scraped,
    )
    art13 = ScrapedArticle(
        id=13, news_source_id=1, article_url="http://a13",
        scraped_title="Candidate 2", scraped_content="Content 2",
        status=ArticleStatus.scraped,
    )
    # Excluded candidates
    art14 = ScrapedArticle(
        id=14, news_source_id=1, article_url="http://a14",
        scraped_title="Old Candidate", scraped_content="Old content",
        status=ArticleStatus.scraped,
    )
    art15 = ScrapedArticle(
        id=15, news_source_id=1, article_url="http://a15",
        scraped_title="Skipped Candidate", scraped_content="Skipped content",
        status=ArticleStatus.scraped,
    )
    art16 = ScrapedArticle(
        id=16, news_source_id=1, article_url="http://a16",
        scraped_title="Failed Candidate", scraped_content="Failed content",
        status=ArticleStatus.scraped,
    )
    art17 = ScrapedArticle(
        id=17, news_source_id=1, article_url="http://a17",
        scraped_title="Other FP Candidate", scraped_content="Other FP content",
        status=ArticleStatus.scraped,
    )
    db_session.add_all([art10, art12, art13, art14, art15, art16, art17])
    db_session.commit()

    # 1. Valid recent job (1h ago)
    j1 = PublishJob(
        id=1, fanpage_id=1, source_article_id=12,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        created_at=now - timedelta(hours=1),
    )
    # 2. Valid older job (5h ago)
    j2 = PublishJob(
        id=2, fanpage_id=1, source_article_id=13,
        content_type=ContentType.news_content,
        status=PublishJobStatus.published,
        created_at=now - timedelta(hours=5),
    )
    # 3. Same article job (source_article_id == 10) -> excluded
    j_same = PublishJob(
        id=3, fanpage_id=1, source_article_id=10,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        created_at=now - timedelta(hours=2),
    )
    # 4. Old job (> 48h ago) -> excluded by window
    j_old = PublishJob(
        id=4, fanpage_id=1, source_article_id=14,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        created_at=now - timedelta(hours=50),
    )
    # 5. Skipped job -> excluded by status
    j_skip = PublishJob(
        id=5, fanpage_id=1, source_article_id=15,
        content_type=ContentType.news_content,
        status=PublishJobStatus.skipped,
        created_at=now - timedelta(hours=3),
    )
    # 6. Failed job -> excluded by status
    j_fail = PublishJob(
        id=6, fanpage_id=1, source_article_id=16,
        content_type=ContentType.news_content,
        status=PublishJobStatus.failed,
        created_at=now - timedelta(hours=4),
    )
    # 7. Other fanpage job (fanpage_id == 2) -> excluded
    j_other_fp = PublishJob(
        id=7, fanpage_id=2, source_article_id=17,
        content_type=ContentType.news_content,
        status=PublishJobStatus.pending_design,
        created_at=now - timedelta(hours=2),
    )

    db_session.add_all([j1, j2, j_same, j_old, j_skip, j_fail, j_other_fp])
    db_session.commit()

    res = recent_story_jobs(db_session, fanpage_id=1, article_id=10, now=now)

    assert len(res) == 2
    # newest first
    assert res[0][0].id == j1.id
    assert res[0][1].id == art12.id
    assert res[1][0].id == j2.id
    assert res[1][1].id == art13.id
