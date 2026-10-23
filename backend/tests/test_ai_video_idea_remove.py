"""Tests: reject() now accepts approved ideas (remove from queue)."""
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text, DefaultClause
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler

# SQLite does not support ARRAY; patch before model imports touch the dialect.
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"

from app.models.ai_video import VideoIdea
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_ideas
from app.services.ai_video_ideas import ConflictError

# TargetFanpage has ARRAY columns with Postgres-specific server_default '{}'.
# Patch them to an empty string so SQLite table creation works.
_ARRAY_COLS = [
    "mode2_gallery_keywords",
    "mode2_gallery_niches",
    "radar_niches",
    "caption_must_include",
    "caption_must_avoid",
]


@pytest.fixture(scope="module")
def sqlite_engine():
    originals = {}
    for col_name in _ARRAY_COLS:
        col = TargetFanpage.__table__.columns.get(col_name)
        if col is not None:
            originals[col_name] = col.server_default
            col.server_default = DefaultClause(text("''"))

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Only create the tables reject() actually touches.
    for table in [TargetFanpage.__table__, VideoIdea.__table__]:
        table.create(bind=engine)

    yield engine

    for table in [VideoIdea.__table__, TargetFanpage.__table__]:
        table.drop(bind=engine, checkfirst=True)
    engine.dispose()

    for col_name, orig in originals.items():
        col = TargetFanpage.__table__.columns.get(col_name)
        if col is not None:
            col.server_default = orig


@pytest.fixture
def db(sqlite_engine):
    Session = sessionmaker(bind=sqlite_engine)
    session = Session()
    try:
        session.query(VideoIdea).delete()
        session.query(TargetFanpage).delete()
        session.commit()
        yield session
    finally:
        session.rollback()
        session.close()


def _fanpage(session):
    fp = TargetFanpage(
        repliz_account_id=f"remove-{uuid4().hex}",
        name=f"Remove {uuid4().hex}",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=1,
        ai_video_min_score=None,
        ai_video_clips=3,
    )
    session.add(fp)
    session.flush()
    return fp


def _idea(session, fanpage, status):
    idea = VideoIdea(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic=f"topic-{uuid4().hex}",
        status=status,
        score=85,
        rationale="test rationale",
    )
    session.add(idea)
    session.flush()
    return idea


def test_reject_approved_idea_sets_rejected(db):
    """Approved idea can be removed: status becomes rejected, decided_by set."""
    fp = _fanpage(db)
    idea = _idea(db, fp, "approved")
    idea_id = idea.id
    db.commit()

    result = ai_video_ideas.reject(db, idea, "owner")
    assert result.status == "rejected"
    assert result.decided_by == "owner"
    assert result.decided_at is not None
    assert db.get(VideoIdea, idea_id).status == "rejected"


def test_reject_queued_idea_raises_conflict(db):
    """Queued idea cannot be rejected — must cancel the job instead."""
    fp = _fanpage(db)
    idea = _idea(db, fp, "queued")
    db.commit()

    with pytest.raises(ConflictError):
        ai_video_ideas.reject(db, idea, "owner")


def test_reject_done_idea_raises_conflict(db):
    """Done idea cannot be rejected."""
    fp = _fanpage(db)
    idea = _idea(db, fp, "done")
    db.commit()

    with pytest.raises(ConflictError):
        ai_video_ideas.reject(db, idea, "owner")


def test_reject_failed_idea_raises_conflict(db):
    """Failed idea cannot be rejected."""
    fp = _fanpage(db)
    idea = _idea(db, fp, "failed")
    db.commit()

    with pytest.raises(ConflictError):
        ai_video_ideas.reject(db, idea, "owner")


def test_reject_proposed_idea_still_works(db):
    """Proposed idea reject still works (regression guard)."""
    fp = _fanpage(db)
    idea = _idea(db, fp, "proposed")
    idea_id = idea.id
    db.commit()

    result = ai_video_ideas.reject(db, idea, "owner")
    assert result.status == "rejected"
    assert db.get(VideoIdea, idea_id).status == "rejected"
