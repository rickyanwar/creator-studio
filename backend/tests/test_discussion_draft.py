"""Tests for discussion ideas AI draft endpoint and service (TASK C4)."""

import json
import sqlite3
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.discussion_content_ideas import DiscussionContentIdea
from app.models.target_fanpages import TargetFanpage
from app.services.news_copywriter import DiscussionCopy, draft_discussion_ideas

sqlite3.register_adapter(list, json.dumps)


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


@pytest.fixture
def db_session():
    """Isolated SQLite in-memory DB session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TargetFanpage.__table__.create(bind=engine)
    DiscussionContentIdea.__table__.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """TestClient with DB and user dependency overrides."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "admin_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def active_brand(db_session):
    brand = TargetFanpage(
        name="Motorsport Central",
        discussion_enabled=True,
        discussion_topic_mode="both",
        discussion_label_mode="both",
        caption_language="en",
        caption_tone="provocative and engaging",
        caption_max_length=300,
        caption_hashtag_count=5,
    )
    db_session.add(brand)
    db_session.commit()
    db_session.refresh(brand)
    return brand


@pytest.fixture
def disabled_brand(db_session):
    brand = TargetFanpage(
        name="Quiet Fanpage",
        discussion_enabled=False,
    )
    db_session.add(brand)
    db_session.commit()
    db_session.refresh(brand)
    return brand


def _mock_copies(count: int = 3):
    return [
        DiscussionCopy(
            label="DISCUSSION" if i % 2 == 0 else "HOT TAKE",
            question=f"Is driver {i} the greatest of all time?",
            subject_name=f"Driver {i}",
            caption=f"Debate time for driver {i}! Drop your comments below. #f1 #racing",
            provider="router",
        )
        for i in range(1, count + 1)
    ]


def test_draft_ideas_success(client, db_session, active_brand):
    """POST /fanpages/{id}/discussion-ideas/draft creates and returns pending ideas."""
    fake_copies = _mock_copies(3)
    with patch("app.services.news_copywriter.draft_discussion_ideas", return_value=fake_copies) as mock_service:
        res = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 3, "topic_hint": "championship battle"},
        )
        assert res.status_code == 200
        data = res.json()
        assert len(data) == 3

        mock_service.assert_called_once()
        _, kwargs = mock_service.call_args
        assert kwargs["count"] == 3
        assert kwargs["topic_hint"] == "championship battle"

        for idx, item in enumerate(data, start=1):
            assert item["id"] is not None
            assert item["label"] in ("DISCUSSION", "HOT TAKE")
            assert item["question"] == f"Is driver {idx} the greatest of all time?"
            assert item["subject_name"] == f"Driver {idx}"
            assert item["status"] == "pending"
            assert item["source_type"] == "ai_draft"

        # Verify rows in database
        rows = (
            db_session.query(DiscussionContentIdea)
            .filter_by(fanpage_id=active_brand.id)
            .order_by(DiscussionContentIdea.id.asc())
            .all()
        )
        assert len(rows) == 3
        assert rows[0].question == "Is driver 1 the greatest of all time?"
        assert rows[0].status == "pending"
        assert rows[0].source_type == "ai_draft"


def test_draft_ideas_default_count(client, active_brand):
    """Empty JSON or omitted body defaults to count=5."""
    fake_copies = _mock_copies(5)
    with patch("app.services.news_copywriter.draft_discussion_ideas", return_value=fake_copies) as mock_service:
        res = client.post(f"/fanpages/{active_brand.id}/discussion-ideas/draft", json={})
        assert res.status_code == 200
        assert len(res.json()) == 5
        assert mock_service.call_args[1]["count"] == 5
        assert mock_service.call_args[1]["topic_hint"] is None


def test_draft_ideas_count_bounds(client, active_brand):
    """count must be between 1 and 10; out-of-bounds returns 422."""
    res_zero = client.post(
        f"/fanpages/{active_brand.id}/discussion-ideas/draft",
        json={"count": 0},
    )
    assert res_zero.status_code == 422

    res_eleven = client.post(
        f"/fanpages/{active_brand.id}/discussion-ideas/draft",
        json={"count": 11},
    )
    assert res_eleven.status_code == 422

    fake_copies = _mock_copies(1)
    with patch("app.services.news_copywriter.draft_discussion_ideas", return_value=fake_copies):
        res_one = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 1},
        )
        assert res_one.status_code == 200

    fake_copies_10 = _mock_copies(10)
    with patch("app.services.news_copywriter.draft_discussion_ideas", return_value=fake_copies_10):
        res_ten = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 10},
        )
        assert res_ten.status_code == 200


def test_draft_ideas_topic_hint_max_length(client, active_brand):
    """topic_hint <= 200 chars; > 200 returns 422."""
    res_too_long = client.post(
        f"/fanpages/{active_brand.id}/discussion-ideas/draft",
        json={"count": 2, "topic_hint": "x" * 201},
    )
    assert res_too_long.status_code == 422

    fake_copies = _mock_copies(2)
    with patch("app.services.news_copywriter.draft_discussion_ideas", return_value=fake_copies):
        res_ok = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 2, "topic_hint": "x" * 200},
        )
        assert res_ok.status_code == 200


def test_draft_ideas_brand_discussion_disabled_409(client, disabled_brand):
    """Brand without discussion mode enabled returns 409 Conflict."""
    res = client.post(
        f"/fanpages/{disabled_brand.id}/discussion-ideas/draft",
        json={"count": 3},
    )
    assert res.status_code == 409
    assert "Discussion mode is disabled" in res.json()["detail"]


def test_draft_ideas_brand_not_found_404(client):
    """Non-existent brand returns 404."""
    res = client.post("/fanpages/999999/discussion-ideas/draft", json={"count": 3})
    assert res.status_code == 404


def test_draft_ideas_ai_failure_502(client, active_brand):
    """AI failure raises 502 Bad Gateway."""
    with patch("app.services.news_copywriter.draft_discussion_ideas", side_effect=RuntimeError("AI model error")):
        res = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 3},
        )
        assert res.status_code == 502
        assert "AI drafting failed" in res.json()["detail"]


def test_draft_ideas_rate_limit_running_draft_429(client, active_brand):
    """Max 1 running draft per brand — active draft locks second request."""
    from app.api.fanpages import _acquire_draft_lock, _release_draft_lock

    acquired, r_client, r_key = _acquire_draft_lock(active_brand.id)
    assert acquired is True

    try:
        res = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 3},
        )
        assert res.status_code == 429
        assert "already running" in res.json()["detail"]
    finally:
        _release_draft_lock(active_brand.id, r_client, r_key)

    # After release, call succeeds
    fake_copies = _mock_copies(2)
    with patch("app.services.news_copywriter.draft_discussion_ideas", return_value=fake_copies):
        res2 = client.post(
            f"/fanpages/{active_brand.id}/discussion-ideas/draft",
            json={"count": 2},
        )
        assert res2.status_code == 200


def test_service_draft_discussion_ideas_parsing():
    """Unit test news_copywriter.draft_discussion_ideas prompt and response parsing."""
    brand = TargetFanpage(
        id=42,
        name="UFC Arena",
        discussion_enabled=True,
        discussion_topic_mode="evergreen",
        discussion_label_mode="discussion",
        caption_language="en",
        caption_tone="analytical",
        caption_max_length=280,
        caption_hashtag_count=3,
        caption_must_avoid=["clickbait", "rumor"],
    )

    sample_json = json.dumps([
        {
            "label": "DISCUSSION",
            "question": "Is Jon Jones the undisputed MMA GOAT?",
            "subject": "Jon Jones",
            "caption": "His resume speaks for itself. What is your take? Drop a comment! #ufc #mma #goat",
        },
        {
            "label": "HOT TAKE",
            "question": "Islam Makhachev surpasses Khabib in prime form.",
            "subject": "Islam Makhachev",
            "caption": "More complete striker and elite grappling. Do you agree? #ufc #grappling #mma",
        },
    ])

    with patch("app.services.news_copywriter._generate_with_fallback") as mock_gen:
        def _fake_gen(prompt, parse_fn, *args, **kwargs):
            return parse_fn(f"```json\n{sample_json}\n```"), "router"

        mock_gen.side_effect = _fake_gen

        results = draft_discussion_ideas(brand, count=2, topic_hint="GOAT debate")
        assert len(results) == 2
        assert results[0].label == "DISCUSSION"
        assert results[0].question == "Is Jon Jones the undisputed MMA GOAT?"
        assert results[0].subject_name == "Jon Jones"
        assert results[0].provider == "router"
        assert results[1].label == "HOT TAKE"

        # Check prompt contents
        prompt_arg = mock_gen.call_args[0][0]
        assert "UFC Arena" in prompt_arg
        assert "TOPIC / THEME HINT (steer ideas toward this theme or topic): GOAT debate" in prompt_arg
        assert "BANNED WORDS/CONCEPTS (do not use in question or caption): clickbait, rumor" in prompt_arg
