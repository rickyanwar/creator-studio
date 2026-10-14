import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import GflowAccount, VideoIdea, VideoJob
from app.models.research import ResearchFeedback
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services import ai_video_dispatch, api_tokens, research_desk
import app.tasks.ai_video_plan as planner


@pytest.fixture(scope="module", autouse=True)
def setup_db():
    Base.metadata.create_all(bind=engine)


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def clean(db):
    others = db.query(TargetFanpage).filter(TargetFanpage.ai_video_enabled == True).all()  # noqa: E712
    for fp in others:
        fp.ai_video_enabled = False
    row = research_desk.get_settings_row(db)
    row.ai_video_researcher, row.muse_research_mode, row.muse_pilot_packs = "muse", "pilot", 3
    row.ai_video_idea_bank_days = 3
    row.research_references, row.research_last_report = None, None
    db.commit()
    created = []
    yield created
    db.rollback()
    ids = [fp_id for fp_id in created]
    db.query(VideoJob).filter(VideoJob.fanpage_id.in_(ids)).delete(synchronize_session=False)
    db.query(ResearchFeedback).filter(ResearchFeedback.idea_id.in_(
        db.query(VideoIdea.id).filter(VideoIdea.fanpage_id.in_(ids)))).delete(synchronize_session=False)
    db.query(VideoIdea).filter(VideoIdea.fanpage_id.in_(ids)).delete(synchronize_session=False)
    db.query(TargetFanpage).filter(TargetFanpage.id.in_(ids)).delete(synchronize_session=False)
    db.commit()


def make_fanpage(db, created, name="Test Idea Bank Page", niches=("f1",), **kw):
    fp = TargetFanpage(
        repliz_account_id=f"ib-{uuid.uuid4().hex[:12]}",
        name=name,
        is_active=True,
        ai_video_enabled=True,
        mode2_gallery_niches=list(niches),
        caption_language="en",
        target_country="GB",
        ai_video_clips=3,
        ai_video_clips_min=2,
        ai_video_min_score=70,
        **kw,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    created.append(fp.id)
    return fp


def test_channel_need_uses_bank_and_review_cap(db, clean):
    row = research_desk.get_settings_row(db)
    row.ai_video_idea_bank_days = 3
    row.muse_pilot_packs = 3
    db.commit()

    fp = make_fanpage(db, clean, ai_video_daily_count=2)

    # 0 waiting -> need 3 (review cap: bank 6-0=6, review 3-0=3 -> min(6, 3) = 3)
    assert research_desk.channel_need(db, fp, row) == 3

    # Add 5 approved ideas (source "hermes") -> need 1 (bank 6-5=1, review 3-0=3 -> min(1, 3) = 1)
    for i in range(5):
        db.add(VideoIdea(
            fanpage_id=fp.id,
            template_key="motorsport_tech_explainer",
            topic=f"Hermes approved idea {i}",
            status="approved",
            source="hermes",
            score=80,
        ))
    db.commit()
    assert research_desk.channel_need(db, fp, row) == 1

    # Add 1 pilot idea -> need 0 (bank 6-6=0, review 3-1=2 -> min(0, 2) = 0)
    db.add(VideoIdea(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        topic="Pilot idea 1",
        status="pilot",
        source="muse",
        score=80,
    ))
    db.commit()
    assert research_desk.channel_need(db, fp, row) == 0


def test_approved_muse_ideas_free_review_slots(db, clean):
    row = research_desk.get_settings_row(db)
    row.ai_video_idea_bank_days = 3
    row.muse_pilot_packs = 2
    db.commit()

    fp = make_fanpage(db, clean, ai_video_daily_count=1)

    # 2 muse ideas with status "pilot" -> need 0 (bank 3-2=1, review 2-2=0 -> min(1, 0) = 0)
    i1 = VideoIdea(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        topic="Muse pilot 1",
        status="pilot",
        source="muse",
        score=80,
    )
    i2 = VideoIdea(
        fanpage_id=fp.id,
        template_key="motorsport_tech_explainer",
        topic="Muse pilot 2",
        status="pilot",
        source="muse",
        score=80,
    )
    db.add_all([i1, i2])
    db.commit()
    assert research_desk.channel_need(db, fp, row) == 0

    # Set both to "approved" -> need 1 (bank 3-2=1, review 2-0=2 -> min(1, 2) = 1)
    i1.status = "approved"
    i2.status = "approved"
    db.commit()
    assert research_desk.channel_need(db, fp, row) == 1


def test_brief_orders_emptiest_channel_first_and_hides_coverage(db, clean):
    row = research_desk.get_settings_row(db)
    row.ai_video_idea_bank_days = 3
    row.muse_pilot_packs = 3
    row.ai_video_researcher = "muse"
    row.muse_research_mode = "pilot"
    db.commit()

    fp_a = make_fanpage(db, clean, name="Channel A", niches=("f1",), ai_video_daily_count=1)
    fp_b = make_fanpage(db, clean, name="Channel B", niches=("f1",), ai_video_daily_count=1)

    # Channel A has 2 approved ideas, B has 0
    db.add_all([
        VideoIdea(
            fanpage_id=fp_a.id,
            template_key="motorsport_tech_explainer",
            topic="Idea A1",
            status="approved",
            source="ai",
        ),
        VideoIdea(
            fanpage_id=fp_a.id,
            template_key="motorsport_tech_explainer",
            topic="Idea A2",
            status="approved",
            source="ai",
        ),
    ])
    db.commit()

    brief = research_desk.build_brief(db)
    assert brief["action"] == "research"
    f1_niche = next(n for n in brief["niches"] if n["niche"] == "f1")
    channel_ids = [c["channel_id"] for c in f1_niche["channels"]]
    assert channel_ids == [fp_b.id, fp_a.id]

    brief_text = json.dumps(brief)
    assert "_coverage" not in brief_text


def test_writer_off_while_muse_researches(db, clean, monkeypatch):
    row = research_desk.get_settings_row(db)
    orig_researcher = row.ai_video_researcher
    orig_days = row.ai_video_idea_bank_days
    row.ai_video_researcher = "muse"
    db.commit()

    calls = []
    monkeypatch.setattr(planner, "SessionLocal", lambda: db)
    monkeypatch.setattr(planner, "write_ideas", lambda db, fp, need: calls.append((fp.id, need)))

    try:
        make_fanpage(db, clean, ai_video_daily_count=1)
        planner.top_up_ai_video_ideas.run()
        assert len(calls) == 0
    finally:
        row = research_desk.get_settings_row(db)
        row.ai_video_researcher = orig_researcher
        row.ai_video_idea_bank_days = orig_days
        db.commit()


def test_writer_fills_emptiest_first(db, clean, monkeypatch):
    row = research_desk.get_settings_row(db)
    orig_researcher = row.ai_video_researcher
    orig_days = row.ai_video_idea_bank_days
    row.ai_video_researcher = "hermes"
    row.ai_video_idea_bank_days = 3
    db.commit()

    calls = []
    monkeypatch.setattr(planner, "SessionLocal", lambda: db)
    monkeypatch.setattr(planner, "write_ideas", lambda db, fp, need: (calls.append(fp.id), need)[1])

    try:
        fp_x = make_fanpage(db, clean, name="Fanpage X", ai_video_daily_count=1)
        fp_y = make_fanpage(db, clean, name="Fanpage Y", ai_video_daily_count=1)

        # X has 2 waiting ideas, Y has 0
        db.add_all([
            VideoIdea(
                fanpage_id=fp_x.id,
                template_key="motorsport_tech_explainer",
                topic="X1",
                status="proposed",
                source="hermes",
            ),
            VideoIdea(
                fanpage_id=fp_x.id,
                template_key="motorsport_tech_explainer",
                topic="X2",
                status="proposed",
                source="hermes",
            ),
        ])
        db.commit()

        planner.top_up_ai_video_ideas.run()
        fixture_calls = [fid for fid in calls if fid in (fp_x.id, fp_y.id)]
        assert fixture_calls == [fp_y.id, fp_x.id]
    finally:
        row = research_desk.get_settings_row(db)
        row.ai_video_researcher = orig_researcher
        row.ai_video_idea_bank_days = orig_days
        db.commit()


def test_claim_fair_share_across_fanpages(db, clean, monkeypatch):
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)

    s = db.query(Settings).first()
    if not s:
        s = Settings(id=1, ai_video_worker_enabled=True, ai_video_credit_reserve=0)
        db.add(s)
    else:
        s.ai_video_worker_enabled = True
        s.ai_video_credit_reserve = 0
    db.commit()

    fp_p = make_fanpage(db, clean, name="Fanpage P")
    fp_q = make_fanpage(db, clean, name="Fanpage Q")

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    # Fanpage P has a job that started 1 minute ago (status done)
    past_job_p = VideoJob(
        fanpage_id=fp_p.id,
        template_key="motorsport_tech_explainer",
        title="Past Job P",
        caption="Cap P",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="done",
        started_at=now - timedelta(minutes=1),
        created_at=now - timedelta(minutes=2),
    )
    db.add(past_job_p)

    # Queue: P's job created first, Q's job created second
    job_p = VideoJob(
        fanpage_id=fp_p.id,
        template_key="motorsport_tech_explainer",
        title="Queued Job P",
        caption="Cap P",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        created_at=now - timedelta(minutes=10),
    )
    job_q = VideoJob(
        fanpage_id=fp_q.id,
        template_key="motorsport_tech_explainer",
        title="Queued Job Q",
        caption="Cap Q",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[{"index": 1}],
        credits_per_clip=15,
        credits_estimate=15,
        status="queued",
        created_at=now - timedelta(minutes=5),
    )
    worker_id = f"w-{uuid.uuid4().hex[:6]}"
    acc = GflowAccount(
        worker_id=worker_id,
        profile="prof_1",
        authenticated=True,
        status="ok",
        credits=100,
    )
    db.add_all([job_p, job_q, acc])
    db.commit()

    try:
        claimed = ai_video_dispatch.claim_job(db, worker_id)
        assert claimed is not None
        claimed_job, claimed_acc = claimed
        assert claimed_job.id == job_q.id
        assert claimed_job.fanpage_id == fp_q.id
    finally:
        db.delete(acc)
        db.commit()
