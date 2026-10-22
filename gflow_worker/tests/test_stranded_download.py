from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest

from gflow_worker.gflow import FAILED, GflowResult, SUCCESS
from .conftest import calls, payload


def test_i2v_no_reply_stranded_download_recovers_on_second_poll(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append((args, kwargs))
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        runner.gflow,
        "stranded_candidates",
        lambda profile, project, submit_time: [
            {
                "media_id": "stranded-pending-1",
                "project_id": project,
                "created_at": now.isoformat(),
                "status": "pending",
            }
        ],
    )

    dl_calls = 0
    orig_download = runner.gflow.download_media

    def mock_download(media_id, profile, out):
        nonlocal dl_calls
        dl_calls += 1
        if dl_calls == 1:
            return GflowResult(ok=False, payload={}, exit_code=7, message="no signed media URL within 45s")
        return orig_download(media_id, profile=profile, out=out)

    monkeypatch.setattr(runner.gflow, "download_media", mock_download)

    runner.run_job(payload(2))

    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    assert len(i2v_calls) == 1
    assert dl_calls == 2
    clip_done_events = [e for e in studio.events("/progress") if e["event"] == "clip_done"]
    assert len(clip_done_events) == 2
    assert clip_done_events[1]["media_id"] == "stranded-pending-1"


def test_failed_row_never_offered_no_download(cfg, studio, runner, monkeypatch):
    runner._sleep = lambda s: None
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append((args, kwargs))
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    now = datetime.now(timezone.utc)
    monkeypatch.setattr(
        runner.gflow,
        "list_videos",
        lambda profile, limit=20: [
            {
                "media_id": "failed-media-1",
                "project_id": "project-1",
                "created_at": now.isoformat(),
                "status": "MEDIA_GENERATION_STATUS_FAILED",
            },
            {
                "media_id": "failed-media-2",
                "project_id": "project-1",
                "created_at": now.isoformat(),
                "status": "FAILED",
            },
        ],
    )

    download_calls = []
    monkeypatch.setattr(runner.gflow, "download_media", lambda *a, **kw: download_calls.append(a))

    runner.run_job(payload(2))

    assert len(download_calls) == 0
    assert not studio.uploads
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "transient"
    assert "clicking submit" in fail_events[0]["message"]
    assert len(i2v_calls) == 1


def test_no_candidates_at_all_fails_sleep_bounded(cfg, studio, runner, monkeypatch):
    slept = []
    runner._sleep = lambda s: slept.append(s)

    def mock_i2v(*args, **kwargs):
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)
    monkeypatch.setattr(runner.gflow, "list_videos", lambda profile, limit=20: [])

    runner.run_job(payload(2))

    assert not studio.uploads
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == "transient"
    assert "clicking submit" in fail_events[0]["message"]
    assert sum(slept) <= 240
    assert sum(slept) == 240
    assert all(s == 30 for s in slept)


def test_cancellation_during_stranded_wait(cfg, studio, runner, monkeypatch):
    i2v_calls = []

    def mock_i2v(*args, **kwargs):
        i2v_calls.append(args)
        return GflowResult(
            ok=False,
            payload={},
            exit_code=9,
            error_class="TransportError",
            retryable=True,
            message="no YhhmEf reply within 60s of clicking submit",
        )

    monkeypatch.setattr(runner.gflow, "i2v", mock_i2v)

    def mock_candidates(*args, **kwargs):
        runner.apply_cancels([1])
        return []

    monkeypatch.setattr(runner.gflow, "stranded_candidates", mock_candidates)

    runner.run_job(payload(2))

    assert not studio.uploads
    assert not studio.events("/fail")
    assert len(i2v_calls) == 1


def test_stranded_candidates_unit_filters_and_sorts(runner, monkeypatch):
    submit_time = datetime(2026, 10, 22, 12, 0, 0, tzinfo=timezone.utc)
    t_before = (submit_time - timedelta(seconds=10)).isoformat()
    t_equal = submit_time.isoformat()
    t_after_1 = (submit_time + timedelta(seconds=10)).isoformat()
    t_after_2 = (submit_time + timedelta(seconds=30)).isoformat()

    rows = [
        "invalid row",
        {"media_id": "wrong-proj", "project_id": "other-proj", "created_at": t_after_1, "status": "pending"},
        {"media_id": "has-path", "project_id": "p1", "local_path": "/tmp/a.mp4", "created_at": t_after_1, "status": "pending"},
        {"media_id": "no-created", "project_id": "p1", "status": "pending"},
        {"media_id": "before-submit", "project_id": "p1", "created_at": t_before, "status": "pending"},
        {"media_id": "failed-const", "project_id": "p1", "created_at": t_after_1, "status": FAILED},
        {"media_id": "failed-str", "project_id": "p1", "created_at": t_after_1, "status": "FAILED"},
        {"media_id": "valid-equal", "project_id": "p1", "created_at": t_equal, "status": "pending"},
        {"media_id": "valid-after-2", "project_id": "p1", "created_at": t_after_2, "status": "pending"},
        {"media_id": "valid-after-1", "project_id": "p1", "created_at": t_after_1, "status": SUCCESS},
    ]

    monkeypatch.setattr(runner.gflow, "list_videos", lambda profile, limit=20: rows)

    candidates = runner.gflow.stranded_candidates(profile="prof", project="p1", submit_time=submit_time)

    assert [c["media_id"] for c in candidates] == ["valid-after-2", "valid-after-1", "valid-equal"]

    monkeypatch.setattr(runner.gflow, "list_videos", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("error")))
    assert runner.gflow.stranded_candidates(profile="prof", project="p1", submit_time=submit_time) == []

    naive_t = datetime(2026, 10, 22, 12, 10, 0).isoformat()
    monkeypatch.setattr(runner.gflow, "list_videos", lambda profile, limit=20: [
        {"media_id": "naive-created", "project_id": "p1", "created_at": naive_t, "status": "pending"}
    ])
    res = runner.gflow.stranded_candidates(profile="prof", project="p1", submit_time=submit_time)
    assert [c["media_id"] for c in res] == ["naive-created"]
