"""Tests for Argon early prepare and plan integration (TASK-A5)."""

from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

from app.models.ai_video import VideoIdea, VideoJob
from app.models.settings import Settings
from app.models.target_fanpages import TargetFanpage
from app.services.ai_video_prepare import (
    MAX_IDEAS_PER_RUN,
    MAX_PREPARE_ROUNDS,
    PREPARED_AHEAD_MIN,
    PREPARED_VERSION,
    RETRY_AFTER_FAILED_MIN,
    candidate_ideas,
    idea_fingerprint,
    pick_run_ideas,
    planned_clip_count,
    polish_enabled,
    prepare_idea,
    valid_prepared,
)
from app.services.prompt_polish import PolishConfig, PolishResult, PolishUnavailable
import app.services.ai_video_fill as ai_video_fill
import app.tasks.ai_video_plan as ai_video_plan
import app.tasks.ai_video_prepare as ai_video_prepare

# SQLite compatibility for Postgres ARRAY/JSONB
@compiles(ARRAY, "sqlite")
def _compile_array(element, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(element, compiler, **kw):
    return "TEXT"


test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)

# Strip Postgres server_defaults that SQLite cannot parse
_clips_col = VideoJob.__table__.columns["clips_done"]
_orig_clips = _clips_col.server_default
_clips_col.server_default = None

for _tbl in [
    TargetFanpage.__table__,
    VideoIdea.__table__,
    VideoJob.__table__,
    Settings.__table__,
]:
    _tbl.create(test_engine)

_clips_col.server_default = _orig_clips

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db():
    session = TestSessionLocal()
    fanpage_ids = []
    try:
        yield session, fanpage_ids
    finally:
        session.rollback()
        if fanpage_ids:
            session.query(VideoJob).filter(VideoJob.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.query(VideoIdea).filter(VideoIdea.fanpage_id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.query(TargetFanpage).filter(TargetFanpage.id.in_(fanpage_ids)).delete(synchronize_session=False)
            session.commit()
        session.close()


def _fanpage(session, fanpage_ids, **kwargs):
    fp_kwargs = dict(
        repliz_account_id=f"rep-{uuid4().hex[:12]}",
        name=f"Fanpage {uuid4().hex[:8]}",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
        ai_video_daily_count=2,
        ai_video_min_score=None,
        ai_video_clips=2,
        ai_video_prompt_polish_enabled=False,
    )
    fp_kwargs.update(kwargs)
    fanpage = TargetFanpage(**fp_kwargs)
    session.add(fanpage)
    session.flush()
    fanpage_ids.append(fanpage.id)
    return fanpage


def _idea(session, fanpage, topic="explainer", **kwargs):
    i_kwargs = dict(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        topic=topic,
        status="approved",
        score=90,
        rationale="r",
    )
    i_kwargs.update(kwargs)
    idea = VideoIdea(**i_kwargs)
    session.add(idea)
    session.flush()
    return idea


CFG = PolishConfig(base_url="http://localhost:8098", token="tok", model="gemini-argon")


# ── Tests ──────────────────────────────────────────────────────────────────────

def test_1_prepare_idea_success(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    idea = _idea(session, fp, topic="rear wing aerodynamics", clips=2)
    session.commit()

    orig_clips = [
        {"index": 1, "mode": "t2v", "prompt": "Prompt 1 original"},
        {"index": 2, "mode": "i2v", "prompt": "Prompt 2 original"},
    ]
    pol_clips = [
        {"index": 1, "mode": "t2v", "prompt": "Prompt 1 polished"},
        {"index": 2, "mode": "i2v", "prompt": "Prompt 2 polished"},
    ]
    fill_vars = {"part": "rear wing", "vehicle_preset": "f1"}
    with patch("app.services.ai_video_prepare.fill_idea", return_value=(fill_vars, "Polished Title", "Polished Caption", orig_clips, "gemini")), \
         patch("app.services.ai_video_prepare.polish_clips", return_value=PolishResult(
             ok=True,
             clips=pol_clips,
             changes=["smoothed motion"],
             voice_sentence="A calm voice sentence.",
             ambience_sentence="A calm ambience sentence.",
             attempts=1,
             account="acct-1",
             seconds=6.5,
         )):
        outcome = prepare_idea(session, idea, fp, CFG)

    assert outcome == "prepared"
    session.refresh(idea)
    assert idea.status == "approved"  # unchanged
    assert idea.variables is None  # unchanged
    assert idea.polish_attempts == 1
    assert idea.polish_error is None
    assert idea.prepared_at is not None

    prep = idea.prepared
    assert isinstance(prep, dict)
    assert prep["version"] == PREPARED_VERSION
    assert prep["fingerprint"] == idea_fingerprint(idea)
    assert prep["clips_count"] == 2
    assert prep["polished"] is True
    assert prep["title"] == "Polished Title"
    assert prep["caption"] == "Polished Caption"
    assert prep["variables"] == fill_vars
    assert "cover" not in prep
    assert len(prep["clips"]) == 2
    assert prep["clips"][0]["prompt_original"] == "Prompt 1 original"
    assert prep["clips"][0]["prompt"] == "Prompt 1 polished"
    assert prep["clips"][1]["prompt_original"] == "Prompt 2 original"
    assert prep["clips"][1]["prompt"] == "Prompt 2 polished"


def test_2_polish_not_ok_retries_and_fails(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    idea = _idea(session, fp, topic="turbo lag", clips=2)
    session.commit()

    orig_clips = [{"index": 1, "mode": "t2v", "prompt": "Orig"}]
    fill_return = ({"part": "turbo"}, "T", "C", orig_clips, "gemini")

    with patch("app.services.ai_video_prepare.fill_idea", return_value=fill_return), \
         patch("app.services.ai_video_prepare.polish_clips", return_value=PolishResult(ok=False, problems=["bad logic", "extra quote"])):

        # Round 1
        res1 = prepare_idea(session, idea, fp, CFG)
        assert res1 == "skipped"
        session.refresh(idea)
        assert idea.prepared is None
        assert idea.polish_error == "bad logic; extra quote"
        assert idea.polish_attempts == 1
        assert idea.prepared_at is not None
        assert idea.status == "approved"

        # Round 2
        res2 = prepare_idea(session, idea, fp, CFG)
        assert res2 == "skipped"
        session.refresh(idea)
        assert idea.polish_attempts == 2
        assert idea.prepared_at is not None
        assert idea.status == "approved"

        # Round 3
        res3 = prepare_idea(session, idea, fp, CFG)
        assert res3 == "failed"
        session.refresh(idea)
        assert idea.polish_attempts == 3
        assert idea.prepared_at is not None
        assert idea.status == "failed"
        assert idea.error.startswith("Argon prompt polish failed after 3 rounds:")
        assert "bad logic; extra quote" in idea.error


def test_3_filler_value_error_fails_and_runtime_error_skips(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    idea_val = _idea(session, fp, topic="bad content", clips=2)
    idea_run = _idea(session, fp, topic="router down", clips=2)
    session.commit()

    # Case A: ValueError -> failed
    with patch("app.services.ai_video_prepare.fill_idea", side_effect=ValueError("invalid json from filler")):
        res = prepare_idea(session, idea_val, fp, CFG)
        assert res == "failed"
        session.refresh(idea_val)
        assert idea_val.status == "failed"
        assert "invalid json from filler" in idea_val.error

    # Case B: RuntimeError -> skipped, nothing changed
    with patch("app.services.ai_video_prepare.fill_idea", side_effect=RuntimeError("connection refused")):
        res = prepare_idea(session, idea_run, fp, CFG)
        assert res == "skipped"
        session.refresh(idea_run)
        assert idea_run.status == "approved"
        assert idea_run.error is None
        assert idea_run.polish_attempts == 0


def test_4_polish_unavailable_propagates_and_stops_task(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    i1 = _idea(session, fp, topic="first idea", clips=2)
    i2 = _idea(session, fp, topic="second idea", clips=2)
    session.commit()

    # Test propagation from prepare_idea
    with patch("app.services.ai_video_prepare.fill_idea", return_value=({}, "T", "C", [], "gemini")), \
         patch("app.services.ai_video_prepare.polish_clips", side_effect=PolishUnavailable("no_account_ready", "no accounts", retry_after_s=120)):
        with pytest.raises(PolishUnavailable) as exc_info:
            prepare_idea(session, i1, fp, CFG)
        assert exc_info.value.kind == "no_account_ready"
        assert exc_info.value.retry_after_s == 120

    # Test task stops after first unavailable idea
    i1_id = i1.id
    i2_id = i2.id
    with patch("app.tasks.ai_video_prepare.SessionLocal", TestSessionLocal), \
         patch("app.tasks.ai_video_prepare._redis") as mock_r, \
         patch("app.tasks.ai_video_prepare.get_polish_config", return_value=CFG), \
         patch("app.tasks.ai_video_prepare.project_can_generate", return_value=True), \
         patch("app.tasks.ai_video_prepare.prepare_idea", side_effect=PolishUnavailable("proxy_down", "down")) as mock_prep, \
         patch("app.tasks.ai_video_prepare.record_run") as mock_rec:

        mock_r.return_value.set.return_value = True
        mock_r.return_value.eval.return_value = 1

        ai_video_prepare.prepare_ai_video_ideas()

        assert mock_prep.call_count == 1
        mock_rec.assert_called_once_with(
            prepared=0,
            failed=0,
            unavailable="proxy_down",
            retry_after_s=None,
            detail="down",
        )

        check_s = TestSessionLocal()
        v1 = check_s.get(VideoIdea, i1_id)
        v2 = check_s.get(VideoIdea, i2_id)
        assert v1.prepared is None
        assert v2.prepared is None
        check_s.close()


def test_5_candidate_ideas_filtering_and_ordering(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_min_score=70, ai_video_prompt_polish_enabled=True)

    # 1. Approved idea -> candidate
    i1 = _idea(session, fp, topic="i1 approved", status="approved", score=80)
    # 2. Proposed with score >= min -> candidate
    i2 = _idea(session, fp, topic="i2 proposed high", status="proposed", score=75)
    # 3. Proposed with score < min -> excluded
    i3 = _idea(session, fp, topic="i3 proposed low", status="proposed", score=60)
    # 4. Valid prepared -> excluded
    i4 = _idea(session, fp, topic="i4 prepared", status="approved", score=95)
    i4.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(i4),
        "template_key": i4.template_key,
        "polished": True,
        "clips": [{"index": 1, "prompt": "P"}],
        "clips_count": 2,
    }
    # 5. Disallowed template -> excluded
    i5 = _idea(session, fp, topic="i5 disallowed", status="approved", score=90, template_key="disallowed_template")
    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=["motorsport_tech_explainer"]):
        cands = candidate_ideas(session, fp)

    cand_ids = [c.id for c in cands]
    assert i1.id in cand_ids
    assert i2.id in cand_ids
    assert i3.id not in cand_ids
    assert i4.id not in cand_ids
    assert i5.id not in cand_ids

    # Score desc order: i1 (score 80) then i2 (score 75)
    assert cand_ids == [i1.id, i2.id]

    # When min_score is None, proposed is not candidate
    fp.ai_video_min_score = None
    session.commit()
    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=["motorsport_tech_explainer"]):
        cands_no_min = candidate_ideas(session, fp)
    assert [c.id for c in cands_no_min] == [i1.id]


def test_6_pick_run_ideas_round_robin_max_3(db):
    session, fanpage_ids = db
    fp1 = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    fp2 = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)

    fp1_i1 = _idea(session, fp1, topic="fp1-1", score=90)
    fp1_i2 = _idea(session, fp1, topic="fp1-2", score=80)
    fp1_i3 = _idea(session, fp1, topic="fp1-3", score=70)

    fp2_i1 = _idea(session, fp2, topic="fp2-1", score=85)
    fp2_i2 = _idea(session, fp2, topic="fp2-2", score=75)
    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=[]):
        picked = pick_run_ideas(session, [fp2, fp1])  # passes in reverse id order

    assert MAX_IDEAS_PER_RUN == 3
    assert len(picked) == MAX_IDEAS_PER_RUN  # 3
    # Round-robin: fp1_i1, fp2_i1, fp1_i2
    assert picked[0] == (fp1, fp1_i1)
    assert picked[1] == (fp2, fp2_i1)
    assert picked[2] == (fp1, fp1_i2)


def test_pick_run_ideas_priority_capped_vs_quota(db):
    session, fanpage_ids = db
    fp1 = _fanpage(session, fanpage_ids, ai_video_daily_count=2, ai_video_prompt_polish_enabled=True)
    fp2 = _fanpage(session, fanpage_ids, ai_video_daily_count=2, ai_video_prompt_polish_enabled=True)

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    for idx in range(2):
        session.add(VideoJob(
            fanpage_id=fp1.id,
            template_key="motorsport_tech_explainer",
            title=f"Job {idx}",
            caption="Cap",
            model="veo2",
            aspect="9:16",
            clip_seconds=5,
            clips=[],
            credits_per_clip=10,
            credits_estimate=20,
            status="completed",
            created_at=now,
        ))

    fp1_i1 = _idea(session, fp1, topic="fp1-idea", score=95)
    fp2_i1 = _idea(session, fp2, topic="fp2-idea", score=90)
    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=[]):
        picked = pick_run_ideas(session, [fp1, fp2])

    assert len(picked) == 2
    assert picked[0] == (fp2, fp2_i1)
    assert picked[1] == (fp1, fp1_i1)


def test_pick_run_ideas_priority_quota_with_prep_vs_without_prep(db):
    session, fanpage_ids = db
    fp1 = _fanpage(session, fanpage_ids, ai_video_daily_count=2, ai_video_prompt_polish_enabled=True)
    fp2 = _fanpage(session, fanpage_ids, ai_video_daily_count=2, ai_video_prompt_polish_enabled=True)

    fp1_prep = _idea(session, fp1, topic="fp1-prep", status="approved", score=95)
    fp1_prep.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(fp1_prep),
        "template_key": fp1_prep.template_key,
        "clips_count": 2,
        "polished": True,
        "clips": [{"index": 1, "prompt": "P"}],
    }
    fp1_i2 = _idea(session, fp1, topic="fp1-cand", status="approved", score=80)

    fp2_i1 = _idea(session, fp2, topic="fp2-cand", status="approved", score=90)
    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=[]):
        picked = pick_run_ideas(session, [fp1, fp2])

    assert picked[0] == (fp2, fp2_i1)
    assert picked[1] == (fp1, fp1_i2)


def test_beat_schedule_prepare_ai_video_ideas():
    from app.tasks.celery_app import celery_app
    entry = celery_app.conf.beat_schedule["prepare-ai-video-ideas"]
    assert entry["schedule"] == 120
    assert entry["options"]["expires"] == 110


def test_7_valid_prepared_validation(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids)
    idea = _idea(session, fp, topic="original topic", title="Original Title")
    session.commit()

    idea.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(idea),
        "template_key": idea.template_key,
        "polished": True,
        "clips": [{"index": 1, "prompt": "P"}],
    }

    assert valid_prepared(idea) is not None

    # Fingerprint change (title edited) -> None
    idea.title = "Modified Title"
    assert valid_prepared(idea) is None
    idea.title = "Original Title"
    assert valid_prepared(idea) is not None

    # Version mismatch -> None
    idea.prepared["version"] = 999
    assert valid_prepared(idea) is None
    idea.prepared["version"] = PREPARED_VERSION

    # Not polished -> None
    idea.prepared["polished"] = False
    assert valid_prepared(idea) is None
    idea.prepared["polished"] = True

    # Empty clips -> None
    idea.prepared["clips"] = []
    assert valid_prepared(idea) is None


def test_8_plan_ai_videos_switch_on(db, monkeypatch):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True, ai_video_clips=2)
    idea_prep = _idea(session, fp, topic="ground effect", clips=2)
    session.commit()

    fp_id = fp.id
    idea_prep_id = idea_prep.id

    # Sub-case A: Idea with valid_prepared -> job created with prepared prompts
    prep_data = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(idea_prep),
        "template_key": idea_prep.template_key,
        "clips_count": 2,
        "variables": {"part": "venturi tunnels"},
        "title": "Ground Effect Genius",
        "caption": "How venturi tunnels work. #F1",
        "polished": True,
        "clips": [
            {"index": 1, "mode": "t2v", "prompt": "Polished clip 1"},
            {"index": 2, "mode": "i2v", "prompt": "Polished clip 2"},
        ],
    }
    idea_prep.prepared = prep_data
    session.commit()

    monkeypatch.setattr(ai_video_plan, "SessionLocal", TestSessionLocal)
    with patch("app.tasks.ai_video_plan.project_can_generate", return_value=True), \
         patch("app.tasks.ai_video_plan.prepare_cover") as mock_cover:
        ai_video_plan.plan_ai_videos.run()

    check_s = TestSessionLocal()
    job = check_s.query(VideoJob).filter(VideoJob.fanpage_id == fp_id).first()
    assert job is not None
    assert job.title == "Ground Effect Genius"
    assert job.caption == "How venturi tunnels work. #F1"
    assert job.clips == prep_data["clips"]
    stored_prep = check_s.get(VideoIdea, idea_prep_id)
    assert stored_prep.status == "queued"
    assert stored_prep.variables == prep_data["variables"]
    assert stored_prep.error is None
    mock_cover.assert_called_once()

    # Clean up job for sub-case B
    check_s.delete(job)
    check_s.commit()
    check_s.close()

    # Sub-case B: Unprepared approved idea only -> no job created
    idea_unprep = _idea(session, fp, topic="unprepared idea", clips=2)
    idea_unprep_id = idea_unprep.id
    session.commit()

    with patch("app.tasks.ai_video_plan.project_can_generate", return_value=True):
        ai_video_plan.plan_ai_videos.run()

    check_s2 = TestSessionLocal()
    assert check_s2.query(VideoJob).filter(VideoJob.idea_id == idea_unprep_id).count() == 0

    # Sub-case C: Prepared clips_count != today's clip count -> no job created
    idea_unprep_stored = check_s2.get(VideoIdea, idea_unprep_id)
    idea_unprep_stored.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(idea_unprep_stored),
        "template_key": idea_unprep_stored.template_key,
        "clips_count": 5,  # fitted will be 2
        "variables": {"part": "diffuser"},
        "title": "Diffuser Tech",
        "caption": "Diffuser details.",
        "polished": True,
        "clips": [{"index": 1, "mode": "t2v", "prompt": "P"}],
    }
    check_s2.commit()

    with patch("app.tasks.ai_video_plan.project_can_generate", return_value=True):
        ai_video_plan.plan_ai_videos.run()

    check_s3 = TestSessionLocal()
    assert check_s3.query(VideoJob).filter(VideoJob.idea_id == idea_unprep_id).count() == 0
    check_s2.close()
    check_s3.close()


def test_9_plan_ai_videos_switch_off_calls_filler(db, monkeypatch):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=False, ai_video_clips=2)
    fp_id = fp.id
    idea = _idea(session, fp, topic="active suspension", clips=2)
    idea_id = idea.id
    session.commit()

    filler_return = (
        {"part": "hydraulics"},
        "Active Suspension Title",
        "Active Suspension Caption",
        [{"index": 1, "mode": "t2v", "prompt": "Unpolished Prompt"}],
        "gemini",
    )

    monkeypatch.setattr(ai_video_plan, "SessionLocal", TestSessionLocal)
    with patch("app.tasks.ai_video_plan.project_can_generate", return_value=True), \
         patch("app.tasks.ai_video_plan.fill_idea", return_value=filler_return) as mock_fill, \
         patch("app.tasks.ai_video_plan.prepare_cover"):
        ai_video_plan.plan_ai_videos.run()

    assert mock_fill.call_count == 1
    check_s = TestSessionLocal()
    job = check_s.query(VideoJob).filter(VideoJob.fanpage_id == fp_id).first()
    assert job is not None
    assert job.title == "Active Suspension Title"
    assert job.clips == [{"index": 1, "mode": "t2v", "prompt": "Unpolished Prompt"}]
    check_s.close()


def test_10_pick_livery_skips_prepared_idea_livery(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids)
    idea = _idea(session, fp, topic="livery test")
    idea.prepared = {
        "version": PREPARED_VERSION,
        "variables": {"livery": "scarlet"},
    }
    idea.prepared_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.commit()

    template = MagicMock()
    with patch("app.services.ai_video_fill.niche_liveries", return_value=["scarlet", "papaya"]):
        picked = ai_video_fill.pick_livery(session, fp.id, template)

    assert picked == "papaya"


def test_11_prepare_idea_stores_no_cover_key_and_makes_one_polish_call(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    idea = _idea(session, fp, topic="no argon cover", clips=2)
    session.commit()

    orig_clips = [{"index": 1, "mode": "t2v", "prompt": "P1"}]
    pol_clips = [{"index": 1, "mode": "t2v", "prompt": "P1 pol"}]
    with patch("app.services.ai_video_prepare.fill_idea", return_value=({}, "T", "C", orig_clips, "gemini")), \
         patch("app.services.ai_video_prepare.polish_clips", return_value=PolishResult(ok=True, clips=pol_clips)) as mock_clips:
        outcome = prepare_idea(session, idea, fp, CFG)

    assert outcome == "prepared"
    mock_clips.assert_called_once()
    session.refresh(idea)
    assert idea.prepared is not None
    assert "cover" not in idea.prepared


def test_14_candidate_ideas_skip_recently_failed(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True)
    i1 = _idea(session, fp, topic="recent fail", status="approved", score=80)
    i1.polish_error = "bad prompt"
    i1.prepared_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=10)

    i2 = _idea(session, fp, topic="old fail", status="approved", score=75)
    i2.polish_error = "bad prompt"
    i2.prepared_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=70)

    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=["motorsport_tech_explainer"]):
        cands = candidate_ideas(session, fp)

    cand_ids = [c.id for c in cands]
    assert i1.id not in cand_ids
    assert i2.id in cand_ids


def test_15_candidate_ideas_prepared_ahead_cap(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_daily_count=1, ai_video_prompt_polish_enabled=True)
    # ahead target = max(2, 1) = 2
    i1 = _idea(session, fp, topic="prepared 1", status="approved")
    i1.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(i1),
        "template_key": i1.template_key,
        "polished": True,
        "clips": [{"index": 1, "prompt": "P"}],
        "clips_count": 2,
    }
    i2 = _idea(session, fp, topic="prepared 2", status="approved")
    i2.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(i2),
        "template_key": i2.template_key,
        "polished": True,
        "clips": [{"index": 1, "prompt": "P"}],
        "clips_count": 2,
    }
    i3 = _idea(session, fp, topic="unprepared candidate", status="approved")
    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=["motorsport_tech_explainer"]):
        assert candidate_ideas(session, fp) == []

    # If only 1 idea is prepared (less than ahead_target=2), i3 is returned
    i2.prepared = None
    session.commit()
    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=["motorsport_tech_explainer"]):
        cands = candidate_ideas(session, fp)
        assert [c.id for c in cands] == [i2.id, i3.id]


def test_16_plan_ai_videos_prepared_cover(db, monkeypatch):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True, ai_video_clips=2)
    idea = _idea(session, fp, topic="ground effect", clips=2)
    session.commit()

    cover_data = {
        "lines": ["HOW VENTURI", "TUNNELS WORK?"],
        "highlight": [1],
        "scene": "A scene",
        "image_prompt": "An image prompt",
        "by": "argon",
    }
    prep_data = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(idea),
        "template_key": idea.template_key,
        "clips_count": 2,
        "variables": {"part": "venturi tunnels"},
        "title": "Ground Effect Genius",
        "caption": "How venturi tunnels work. #F1",
        "polished": True,
        "clips": [
            {"index": 1, "mode": "t2v", "prompt": "Polished clip 1"},
            {"index": 2, "mode": "i2v", "prompt": "Polished clip 2"},
        ],
        "cover": cover_data,
    }
    idea.prepared = prep_data
    session.commit()

    monkeypatch.setattr(ai_video_plan, "SessionLocal", TestSessionLocal)
    with patch("app.tasks.ai_video_plan.project_can_generate", return_value=True), \
         patch("app.tasks.ai_video_plan.prepare_cover") as mock_prepare_cover:
        ai_video_plan.plan_ai_videos.run()

    mock_prepare_cover.assert_not_called()
    check_s = TestSessionLocal()
    job = check_s.query(VideoJob).filter(VideoJob.fanpage_id == fp.id).first()
    assert job is not None
    assert job.cover_lines == cover_data["lines"]
    assert job.cover_highlight == cover_data["highlight"]
    assert job.cover_prompt == cover_data["image_prompt"]
    check_s.close()


def test_17_plan_ai_videos_switch_on_unexpected_error_continues(db, monkeypatch):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_prompt_polish_enabled=True, ai_video_clips=2)
    idea = _idea(session, fp, topic="error idea", clips=2)
    session.commit()

    prep_data = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(idea),
        "template_key": idea.template_key,
        "clips_count": 2,
        "variables": {"part": "part"},
        "title": "Title",
        "caption": "Caption",
        "polished": True,
        "clips": [
            {"index": 1, "mode": "t2v", "prompt": "P1"},
            {"index": 2, "mode": "i2v", "prompt": "P2"},
        ],
    }
    idea.prepared = prep_data
    session.commit()

    monkeypatch.setattr(ai_video_plan, "SessionLocal", TestSessionLocal)
    with patch("app.tasks.ai_video_plan.project_can_generate", return_value=True), \
         patch("app.tasks.ai_video_plan._create_and_commit_job", side_effect=RuntimeError("unexpected DB fail")), \
         patch("app.tasks.ai_video_plan.logger.warning") as mock_warn:
        ai_video_plan.plan_ai_videos.run()

    check_s = TestSessionLocal()
    stored_idea = check_s.get(VideoIdea, idea.id)
    assert stored_idea.status == "approved"
    assert stored_idea.error != "LLM unavailable, will retry: RuntimeError: unexpected DB fail"
    mock_warn.assert_called()
    assert any("AI video planning failed for idea" in str(arg) for call in mock_warn.call_args_list for arg in call[0])
    check_s.close()


def test_candidate_ideas_reprepare_when_clip_count_changed(db):
    session, fanpage_ids = db
    fp = _fanpage(session, fanpage_ids, ai_video_daily_count=2, ai_video_clips=2, ai_video_prompt_polish_enabled=True)
    idea = _idea(session, fp, topic="pit stop aerodynamics", status="approved")
    # Initially prepared with clips_count=2
    idea.prepared = {
        "version": PREPARED_VERSION,
        "fingerprint": idea_fingerprint(idea),
        "template_key": idea.template_key,
        "polished": True,
        "clips": [{"index": 1, "prompt": "P1"}, {"index": 2, "prompt": "P2"}],
        "clips_count": 2,
    }
    session.commit()

    with patch("app.services.ai_video_prepare.allowed_video_templates", return_value=["motorsport_tech_explainer"]):
        # Matching clip count -> not a candidate
        assert candidate_ideas(session, fp) == []

        # Fanpage clip setting changes to 3
        fp.ai_video_clips = 3
        session.commit()

        # clips_count (2) != planned_clip_count (3) -> candidate again!
        cands = candidate_ideas(session, fp)
        assert [c.id for c in cands] == [idea.id]

