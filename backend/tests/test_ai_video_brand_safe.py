import copy
import json
from datetime import timedelta
from unittest.mock import Mock

import pytest
from sqlalchemy import DefaultClause, create_engine, event, text
from sqlalchemy.orm import sessionmaker

from app.models.ai_video import VideoIdea, VideoJob
from app.services import ai_video_brand_safe as brand_safe, ai_video_dispatch
from app.services.video_templates import MAX_PROMPT_CHARS
from app.tasks import ai_video_brand_safe as brand_safe_task
from app.tasks.celery_app import celery_app


ORIGINAL = 'Show HONDA wing on bike 93 at 0-10s. Keep "RACE 93" and "HONDA" unchanged.'
REWRITTEN = ORIGINAL.replace("Show HONDA wing", "Show red wing")


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    column = VideoJob.__table__.c.clips_done
    default = column.server_default
    column.server_default = DefaultClause(text("'[]'"))
    try:
        VideoIdea.__table__.create(bind=engine)
        VideoJob.__table__.create(bind=engine)
    finally:
        column.server_default = default
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(ai_video_dispatch, "MIN_CLAIM_GAP_MINUTES", 0)
    monkeypatch.setattr(brand_safe_task, "SessionLocal", lambda: session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def job(db_session):
    idea = VideoIdea(fanpage_id=1, template_key="motorcycle_commercial", topic="Bike", status="queued")
    db_session.add(idea)
    db_session.flush()
    job = VideoJob(
        fanpage_id=1, idea_id=idea.id, template_key="motorcycle_commercial",
        title="Bike", caption="Caption", model="omni-flash", aspect="9:16",
        clip_seconds=10, clips=[{"index": 1, "mode": "t2v", "prompt": ORIGINAL}],
        credits_per_clip=15, credits_estimate=15, status="claimed", worker_id="w1",
        flow_project_id="old-project", clips_done=[{"index": 1}], clip_attempts=2,
    )
    db_session.add(job)
    db_session.commit()
    return job


@pytest.mark.parametrize("payload,valid", [
    ({"prompts": [REWRITTEN]}, True),
    ({"prompts": [REWRITTEN.replace('"RACE 93"', '"RACE 94"')]}, False),
    ({"prompts": [REWRITTEN.replace("0-10s", "0s")]}, False),
    ({"prompts": []}, False),
    ({"prompts": [REWRITTEN + "x" * MAX_PROMPT_CHARS]}, False),
    ({"prompts": ['93 0 10 "RACE 93" "HONDA"']}, False),
    ({"prompts": [None]}, False),
    ([REWRITTEN], False),
    ({}, False),
    ("invalid JSON", False),
])
def test_parse_rewrite(monkeypatch, payload, valid):
    def generate(prompt, parse, *, context):
        assert context == "ai_video_brand_safe"
        assert prompt == brand_safe.BRAND_SAFE_PROMPT + json.dumps([ORIGINAL], ensure_ascii=False, indent=1)
        raw = json.dumps(payload) if payload != "invalid JSON" else payload
        if valid:
            return parse(raw), "test-model"
        with pytest.raises(ValueError):
            parse(raw)
        raise ValueError("fallback exhausted")

    monkeypatch.setattr(brand_safe.news_copywriter, "_generate_with_fallback", generate)
    result = brand_safe.rewrite_brand_safe([{"prompt": ORIGINAL}])
    assert (result is not None) == valid


def test_rewrite_preserves_input_and_clip_metadata(monkeypatch):
    clips = [{"index": 1, "mode": "t2v", "prompt": ORIGINAL},
             {"index": 2, "mode": "i2v", "prompt": ORIGINAL}]
    snapshot = copy.deepcopy(clips)
    monkeypatch.setattr(
        brand_safe.news_copywriter, "_generate_with_fallback",
        lambda prompt, parse, **kw: (parse(json.dumps({"prompts": [REWRITTEN, REWRITTEN]})), "test"),
    )
    result = brand_safe.rewrite_brand_safe(clips)
    assert clips == snapshot
    assert result is not clips
    for old, new in zip(clips, result):
        assert new is not old
        assert new == {**old, "prompt": REWRITTEN, "brand_safe": True}


def test_rewrite_logs_fallback_failure(monkeypatch, caplog):
    monkeypatch.setattr(brand_safe.news_copywriter, "_generate_with_fallback", Mock(side_effect=RuntimeError("offline")))
    assert brand_safe.rewrite_brand_safe([{"prompt": ORIGINAL}]) is None
    assert "offline" in caplog.text


def test_policy_retry_enqueued_after_commit(db_session, job, monkeypatch):
    committed = []
    event.listen(db_session, "after_commit", lambda session: committed.append(True))

    def enqueue(job_id):
        assert committed == [True]
        assert job_id == job.id

    delay = Mock(side_effect=enqueue)
    monkeypatch.setattr(brand_safe_task.rewrite_job_brand_safe, "delay", delay)
    before = ai_video_dispatch._utcnow()
    ai_video_dispatch.record_failure(db_session, job, "w1", 1, "policy", "PUBLIC_ERROR_UNSAFE")
    after = ai_video_dispatch._utcnow()
    assert job.status == "queued"
    assert job.restarts == 1 and job.generation == 2
    assert job.clips_done == [] and job.clip_attempts == 0
    assert job.flow_project_id is None and job.worker_id is None and job.lease_until is None
    assert before + timedelta(hours=2) <= job.not_before <= after + timedelta(hours=2)
    assert job.error.startswith("policy: brand-safe retry pending")
    assert db_session.get(VideoIdea, job.idea_id).status == "queued"
    delay.assert_called_once_with(job.id)


@pytest.mark.parametrize("already_safe,template,restarts", [
    (True, "motorcycle_commercial", 0),
    (False, "health_3d_explainer", 0),
    (False, "motorcycle_commercial", ai_video_dispatch.MAX_RESTARTS),
])
def test_policy_terminal_failure(db_session, job, monkeypatch, already_safe, template, restarts):
    job.template_key = template
    job.clips = [{**job.clips[0], "brand_safe": already_safe}]
    job.restarts = restarts
    delay = Mock()
    monkeypatch.setattr(brand_safe_task.rewrite_job_brand_safe, "delay", delay)
    ai_video_dispatch.record_failure(db_session, job, "w1", 1, "policy", "blocked")
    assert job.status == "failed" and job.finished_at is not None
    idea = db_session.get(VideoIdea, job.idea_id)
    assert idea.status == "failed" and idea.error == job.error
    delay.assert_not_called()


def test_enqueue_failure_is_best_effort(db_session, job, monkeypatch, caplog):
    monkeypatch.setattr(brand_safe_task.rewrite_job_brand_safe, "delay", Mock(side_effect=RuntimeError("broker offline")))
    ai_video_dispatch.record_failure(db_session, job, "w1", 1, "policy", "blocked")
    assert job.status == "queued" and job.not_before is not None
    assert "broker offline" in caplog.text


@pytest.mark.parametrize("success", [True, False])
def test_rewrite_task(db_session, job, monkeypatch, success):
    job.status = "queued"
    job.error = "policy: brand-safe retry pending: blocked"
    job.not_before = ai_video_dispatch._utcnow() + timedelta(hours=2)
    new = [{**job.clips[0], "prompt": REWRITTEN, "brand_safe": True}]
    rewrite = Mock(return_value=new if success else None)
    monkeypatch.setattr(brand_safe_task, "rewrite_brand_safe", rewrite)
    db_session.commit()
    job_id, idea_id = job.id, job.idea_id
    brand_safe_task.rewrite_job_brand_safe(job_id)
    stored = db_session.get(VideoJob, job_id)
    idea = db_session.get(VideoIdea, idea_id)
    if success:
        assert stored.status == "queued" and stored.clips == new
        assert stored.not_before is None
        assert stored.error == "policy: retrying once with brand names removed"
        assert idea.status == "queued"
    else:
        assert stored.status == "failed" and stored.finished_at is not None
        assert idea.status == "failed" and idea.error == stored.error
    rewrite.assert_called_once()


@pytest.mark.parametrize("status,error", [
    ("claimed", "policy: brand-safe retry pending: blocked"),
    ("queued", "policy: retrying once with brand names removed"),
    ("queued", None),
])
def test_rewrite_task_ignores_nonpending_jobs(db_session, job, monkeypatch, status, error):
    job.status, job.error = status, error
    db_session.commit()
    rewrite = Mock()
    monkeypatch.setattr(brand_safe_task, "rewrite_brand_safe", rewrite)
    brand_safe_task.rewrite_job_brand_safe(job.id)
    rewrite.assert_not_called()


def test_rewrite_task_ignores_missing_job(db_session, monkeypatch):
    rewrite = Mock()
    monkeypatch.setattr(brand_safe_task, "rewrite_brand_safe", rewrite)
    brand_safe_task.rewrite_job_brand_safe(999)
    rewrite.assert_not_called()


def test_task_registered_on_gallery_queue():
    assert "app.tasks.ai_video_brand_safe" in celery_app.conf.include
    assert celery_app.conf.task_routes[brand_safe_task.rewrite_job_brand_safe.name] == {"queue": "gallery"}
