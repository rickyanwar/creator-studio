import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import DefaultClause, create_engine, text
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.ai_video import VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.social_publishing import SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_idea_writer as writer


@pytest.fixture(scope="session", autouse=True)
def writer_job_table(request):
    modules = {item.module for item in request.session.items}
    for existing in modules:
        if not existing.__file__.endswith("/test_ai_video_idea_writer.py") or not existing._USE_SQLITE:
            continue
        # The existing writer fixture predates the writer's VideoJob lookup.
        column = VideoJob.__table__.c.clips_done
        default = column.server_default
        column.server_default = DefaultClause(text("'[]'"))
        try:
            VideoJob.__table__.create(bind=existing._test_engine, checkfirst=True)
        finally:
            column.server_default = default


@pytest.fixture
def db_session():
    sqlite3.register_adapter(list, json.dumps)
    sqlite3.register_adapter(dict, json.dumps)
    SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    defaults = []
    for table in (TargetFanpage.__table__, VideoIdea.__table__, VideoJob.__table__,
                  Settings.__table__, SocialDestination.__table__):
        for column in table.columns:
            if column.server_default is not None and "::" in str(column.server_default.arg):
                defaults.append((column, column.server_default))
                column.server_default = DefaultClause(text("'[]'"))
    try:
        for table in (TargetFanpage.__table__, VideoIdea.__table__, VideoJob.__table__,
                      Settings.__table__, SocialDestination.__table__):
            table.create(bind=engine)
    finally:
        for column, default in defaults:
            column.server_default = default
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def fanpage(db_session):
    page = TargetFanpage(
        repliz_account_id="dedup-page", name="MotoGP Page", is_active=True,
        is_connected=True, timezone="UTC", mode2_gallery_niches=["motogp"],
        ai_video_enabled=True, ai_video_daily_count=2,
        ai_video_template="motorsport_tech_explainer",
    )
    db_session.add(page)
    db_session.commit()
    return page


def add_idea(db, page, topic, **kwargs):
    idea = VideoIdea(
        fanpage_id=page.id, template_key="motorsport_tech_explainer", topic=topic,
        **kwargs,
    )
    db.add(idea)
    db.flush()
    return idea


def add_job(db, page, title, days=10, status="done", idea=None):
    db.add(VideoJob(
        fanpage_id=page.id, idea_id=idea.id if idea else None,
        template_key="motorsport_tech_explainer", title=title, caption="Caption",
        model="omni-flash", aspect="9:16", clip_seconds=10, clips=[],
        credits_per_clip=15, credits_estimate=30, status=status,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days),
    ))
    db.flush()


@pytest.mark.parametrize("count,status,error,expected", [
    (4, "rejected", "duplicate subject: already used on channel 3", True),
    (12, "rejected", "duplicate subject: already used on channel 3", False),
    (4, "done", None, False),
    (4, "rejected", None, False),
    (4, "rejected", "quality rejection", False),
])
def test_top_up_duplicate_rejections(db_session, fanpage, monkeypatch, count, status, error, expected):
    from app.tasks import ai_video_plan as planner

    db_session.add(Settings(ai_video_researcher="ai", ai_video_idea_bank_days=1))
    for i in range(count):
        add_idea(db_session, fanpage, f"Previous topic {i}", source="ai", status=status, error=error)
    db_session.commit()
    monkeypatch.setattr(planner, "SessionLocal", lambda: db_session)
    calls = []
    monkeypatch.setattr(planner, "write_ideas", lambda db, page, need: calls.append(need) or need)
    planner.top_up_ai_video_ideas.run()
    assert bool(calls) is expected
    if expected:
        assert calls == [2]


@pytest.fixture
def topic_history(db_session, fanpage):
    other = TargetFanpage(repliz_account_id="other-page", name="Other page")
    db_session.add(other)
    db_session.flush()
    old = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=40)
    idea = add_idea(db_session, other, "Desmodromic valve timing", created_at=old)
    add_job(db_session, other, "Title must not replace idea topic", idea=idea)
    add_job(db_session, other, "Seamless shift gearbox", days=5)
    add_job(db_session, other, "Expired job topic", days=31)
    add_job(db_session, other, "Cancelled job topic", status="cancelled")
    add_idea(db_session, other, "Carbon brake cooling", status="rejected",
             error="duplicate subject: already used on channel 3")
    add_idea(db_session, other, "Manually rejected topic", status="rejected")
    add_idea(db_session, other, "  seamless SHIFT gearbox  ", status="approved",
             created_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1))
    add_idea(db_session, other, "   ", status="approved")
    db_session.commit()


def test_used_topics_and_prompt(db_session, fanpage, topic_history):
    topics = writer.used_topics(db_session)
    assert topics == ["Carbon brake cooling", "seamless SHIFT gearbox", "Desmodromic valve timing"]
    assert writer.recent_topics(db_session) == topics
    prompt = writer.build_idea_prompt(fanpage, writer.writer_templates(fanpage), topics, 2)
    assert "Desmodromic valve timing" in prompt


def test_write_ideas_drops_other_page_duplicate(db_session, fanpage, topic_history, monkeypatch, caplog):
    ideas = [
        {"template_key": "motorsport_tech_explainer", "topic": topic,
         "score": 85, "rationale": "Visual technical explanation"}
        for topic in ("How desmodromic valve timing works", "Tyre contact patch deformation")
    ]
    prompts = []

    def generate(db, prompt, allowed, count):
        prompts.append(prompt)
        return ideas

    monkeypatch.setattr(writer, "_ideas_via_chatgpt", generate)
    with caplog.at_level("INFO", logger=writer.__name__):
        assert writer.write_ideas(db_session, fanpage, 2) == 1
    created = db_session.query(VideoIdea).filter_by(fanpage_id=fanpage.id).all()
    assert [idea.topic for idea in created] == ["Tyre contact patch deformation"]
    assert "Desmodromic valve timing" in prompts[0]
    assert "Mode 8 idea writer dropped duplicate topic" in caplog.text
