"""Tests for the per-clip Mode 8 logic QC task."""

from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import sessionmaker

from app.models.ai_video import VideoClipQc, VideoJob
from app.models.target_fanpages import TargetFanpage
from app.services.prompt_polish import PolishUnavailable
import app.tasks.ai_video_clip_qc as task


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

_clips_done = VideoJob.__table__.columns["clips_done"]
_clips_done_default = _clips_done.server_default
_clips_done.server_default = None
for _table in (TargetFanpage.__table__, VideoJob.__table__, VideoClipQc.__table__):
    _table.create(test_engine)
_clips_done.server_default = _clips_done_default

TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture
def db():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.query(VideoClipQc).delete(synchronize_session=False)
        session.query(VideoJob).delete(synchronize_session=False)
        session.query(TargetFanpage).delete(synchronize_session=False)
        session.commit()
        session.close()


@pytest.fixture
def configured_task(monkeypatch):
    monkeypatch.setattr(task, "SessionLocal", TestSessionLocal)
    monkeypatch.setattr(task.check_clip, "apply_async", MagicMock())
    monkeypatch.setattr(task, "logic_check", MagicMock(return_value=[]))
    monkeypatch.setattr("app.services.prompt_polish.get_polish_config", lambda db: SimpleNamespace())
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", MagicMock(side_effect=lambda prompt, **kwargs: prompt))
    monkeypatch.setattr("app.services.video_templates.get_template", lambda key: SimpleNamespace(name=key))
    monkeypatch.setattr("app.services.video_templates.fanpage_niche", lambda fanpage: "f1")
    return task.check_clip.apply_async, task.logic_check


def _job(db, *, generation=1):
    fanpage = TargetFanpage(
        repliz_account_id=f"rep-{uuid4().hex}",
        name="Test Fanpage",
        is_active=True,
        is_connected=True,
        ai_video_enabled=True,
    )
    db.add(fanpage)
    db.flush()
    job = VideoJob(
        fanpage_id=fanpage.id,
        template_key="motorsport_tech_explainer",
        title="Title",
        caption="Caption",
        model="omni-flash",
        aspect="9:16",
        clip_seconds=10,
        clips=[
            {"index": 1, "mode": "t2v", "prompt": "P1"},
            {"index": 2, "mode": "i2v", "prompt": "P2"},
        ],
        credits_per_clip=15,
        credits_estimate=30,
        status="generating",
        generation=generation,
        clips_done=[],
    )
    db.add(job)
    db.flush()
    return job


def _row(db, job, *, clip_index=1, attempt=1, prompt=None, generation=None, **values):
    clip_path = values.pop("clip_path", f"c{clip_index}a{attempt}")
    row = VideoClipQc(
        video_job_id=job.id,
        generation=job.generation if generation is None else generation,
        clip_index=clip_index,
        attempt=attempt,
        prompt=prompt or f"P{clip_index}",
        clip_path=clip_path,
        **values,
    )
    db.add(row)
    db.flush()
    return row


def _run(db, row):
    db.commit()
    task.check_clip(row.id)
    check = TestSessionLocal()
    result = (check.get(VideoClipQc, row.id), check.get(VideoJob, row.video_job_id))
    check.close()
    return result


def test_pass_accepts_attempt(db, configured_task):
    row = _row(db, _job(db))
    result, job = _run(db, row)
    assert result.verdict == "pass"
    assert result.status == "passed"
    assert job.clips[0]["prompt"] == row.prompt
    assert job.clips[0]["qc"]["accepted_best"] is False


def test_minor_only_passes(db, configured_task):
    task.logic_check.return_value = [{"problem": "x", "severity": "minor"}]
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict == "pass"
    assert result.minor_count == 1


def test_fail_attempt1_repolishes_with_fresh_issues(db, configured_task, monkeypatch):
    issues = [{"problem": "x", "severity": "major"}]
    task.logic_check.return_value = issues
    repolish = MagicMock(return_value="P1b")
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", repolish)
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict == "retry"
    assert result.next_prompt == "P1b"
    assert repolish.call_args.kwargs["issues"] == issues


def test_repolish_value_error_retries_same_prompt(db, configured_task, monkeypatch):
    task.logic_check.return_value = [{"problem": "x", "severity": "major"}]
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", MagicMock(side_effect=ValueError("bad")))
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict == "retry"
    assert result.next_prompt == row.prompt


def test_no_polish_config_retries_same_prompt(db, configured_task, monkeypatch):
    task.logic_check.return_value = [{"problem": "x", "severity": "major"}]
    repolish = MagicMock()
    monkeypatch.setattr("app.services.prompt_polish.get_polish_config", lambda db: None)
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", repolish)
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict == "retry"
    assert result.next_prompt == row.prompt
    repolish.assert_not_called()


def test_vision_error_waits(db, configured_task):
    task.logic_check.side_effect = RuntimeError("vision down")
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.status == "unavailable"
    assert result.verdict is None
    task.check_clip.apply_async.assert_called_once_with((row.id,), countdown=120)


def test_vision_error_after_6h_fails(db, configured_task):
    task.logic_check.side_effect = RuntimeError("vision down")
    row = _row(db, _job(db), created_at=datetime.utcnow() - timedelta(hours=7))
    result, _ = _run(db, row)
    assert result.verdict == "failed"
    assert result.waiting_reason.startswith("clip QC unavailable for 6 h")
    task.check_clip.apply_async.assert_not_called()


def test_polish_unavailable_waits(db, configured_task, monkeypatch):
    task.logic_check.return_value = [{"problem": "x", "severity": "major"}]
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", MagicMock(side_effect=PolishUnavailable("upstream")))
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict is None
    task.check_clip.apply_async.assert_called_once()


def test_unexpected_repolish_error_waits(db, configured_task, monkeypatch):
    task.logic_check.return_value = [{"problem": "x", "severity": "major"}]
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", MagicMock(side_effect=KeyError("x")))
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.verdict is None
    task.check_clip.apply_async.assert_called_once()


def test_attempt4_uses_best_attempt(db, configured_task, monkeypatch):
    job = _job(db)
    for attempt, major_count in ((1, 2), (2, 1), (3, 1)):
        _row(db, job, attempt=attempt, status="failed", verdict="retry",
             issues=[{"problem": str(attempt), "severity": "major"}] * major_count,
             major_count=major_count, minor_count=0)
    row = _row(db, job, attempt=4)
    task.logic_check.return_value = [{"problem": str(i), "severity": "major"} for i in range(3)]
    repolish = MagicMock()
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", repolish)
    result, updated_job = _run(db, row)
    assert result.verdict == "use_attempt"
    assert result.use_attempt == 2
    assert updated_job.clips[0]["prompt"] == "P1"
    assert updated_job.clips[0]["qc"]["accepted_best"] is True
    repolish.assert_not_called()


@pytest.mark.parametrize("limit", [1, 2, 6, 10])
def test_custom_attempt_limit_stops_and_keeps_best(db, configured_task, monkeypatch, limit):
    job = _job(db)
    job.clip_qc_max_attempts = limit
    for attempt in range(1, limit):
        _row(db, job, attempt=attempt, status="failed", verdict="retry",
             issues=[{"problem": "wheel disappears", "severity": "major"}],
             major_count=1, minor_count=0)
    row = _row(db, job, attempt=limit)
    task.logic_check.return_value = [{"problem": "car reverses", "severity": "major"}] * 2
    repolish = MagicMock()
    monkeypatch.setattr("app.services.prompt_polish.repolish_clip", repolish)
    result, updated_job = _run(db, row)
    assert result.verdict == "use_attempt"
    assert result.use_attempt == 1
    assert updated_job.clips[0]["qc"]["accepted_best"] is True
    repolish.assert_not_called()


def test_custom_limit_allows_retry_beyond_four(db, configured_task):
    job = _job(db)
    job.clip_qc_max_attempts = 6
    row = _row(db, job, attempt=4)
    task.logic_check.return_value = [{"problem": "car reverses", "severity": "major"}]
    result, _ = _run(db, row)
    assert result.verdict == "retry"


def test_running_job_uses_snapshot_after_brand_changes(db, configured_task):
    job = _job(db)
    job.clip_qc_max_attempts = 2
    fanpage = db.get(TargetFanpage, job.fanpage_id)
    fanpage.ai_video_clip_qc_max_attempts = 10
    row = _row(db, job, attempt=2)
    task.logic_check.return_value = [{"problem": "car reverses", "severity": "major"}]
    result, _ = _run(db, row)
    assert result.verdict == "use_attempt"


def test_previous_clip_path_uses_chosen_attempt(db, configured_task):
    job = _job(db)
    for attempt in range(1, 5):
        _row(db, job, attempt=attempt, clip_index=1, clip_path=f"c1a{attempt}",
             status="failed" if attempt < 4 else "passed",
             verdict="use_attempt" if attempt == 4 else "retry",
             use_attempt=2 if attempt == 4 else None,
             issues=[] if attempt == 4 else [{"problem": "x", "severity": "major"}],
             major_count=0 if attempt == 4 else 1, minor_count=0)
    row = _row(db, job, clip_index=2, attempt=1)
    _run(db, row)
    assert task.logic_check.call_args.args[2] == "c1a2"


def test_previous_clip_path_after_pass(db, configured_task):
    job = _job(db)
    _row(db, job, clip_index=1, verdict="pass", status="passed", clip_path="c1a1")
    row = _row(db, job, clip_index=2)
    _run(db, row)
    assert task.logic_check.call_args.args[2] == "c1a1"


def test_no_transaction_during_slow_calls(db, configured_task, monkeypatch):
    captured = []

    def factory():
        session = TestSessionLocal()
        captured.append(session)
        return session

    monkeypatch.setattr(task, "SessionLocal", factory)
    task.logic_check.side_effect = lambda *args: (assert_not_in_transaction(captured), [])[-1]
    row = _row(db, _job(db))
    _run(db, row)
    assert captured
    assert all(not session.is_active or not session.in_transaction() for session in captured)
    for session in captured:
        session.close()


def assert_not_in_transaction(sessions):
    assert sessions and sessions[-1].in_transaction() is False


def test_stale_generation_is_ignored(db, configured_task):
    job = _job(db, generation=2)
    row = _row(db, job, generation=1)
    _run(db, row)
    task.logic_check.assert_not_called()


def test_decided_row_is_ignored(db, configured_task):
    row = _row(db, _job(db), verdict="pass")
    _run(db, row)
    task.logic_check.assert_not_called()


def test_claimed_row_is_skipped(db, configured_task):
    row = _row(db, _job(db), processing_until=datetime.utcnow() + timedelta(minutes=10))
    result, _ = _run(db, row)
    assert result.verdict is None
    assert result.processing_until > datetime.utcnow()
    task.logic_check.assert_not_called()


def test_expired_claim_is_taken(db, configured_task):
    row = _row(db, _job(db), processing_until=datetime.utcnow() - timedelta(minutes=1))
    result, _ = _run(db, row)
    assert result.verdict == "pass"


def test_processing_until_cleared_after_verdict(db, configured_task):
    row = _row(db, _job(db))
    result, _ = _run(db, row)
    assert result.processing_until is None

    row = _row(db, _job(db))
    task.logic_check.side_effect = RuntimeError("vision down")
    result, _ = _run(db, row)
    assert result.processing_until is None


def test_job_reclaimed_during_vision_discards_result(db, configured_task, monkeypatch):
    job = _job(db)
    row = _row(db, job)
    other = TestSessionLocal()

    def change_generation(*args):
        current = other.get(VideoJob, job.id)
        current.generation = 2
        other.commit()
        return []

    monkeypatch.setattr(task, "logic_check", MagicMock(side_effect=change_generation))
    try:
        result, updated_job = _run(db, row)
    finally:
        other.close()
    assert result.verdict is None
    assert updated_job.clips == job.clips
