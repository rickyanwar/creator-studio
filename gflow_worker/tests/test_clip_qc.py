from pathlib import Path

import pytest

from gflow_worker.state import JobState
from .conftest import payload


def setup_qc(runner, monkeypatch, responses, *, cancel=False):
    uploads = []
    statuses = list(responses)

    def upload(job_id, generation, index, attempt, prompt, path):
        uploads.append((index, attempt, prompt, Path(path)))

    def status(job_id, generation, index, attempt):
        response = statuses.pop(0)
        if cancel:
            runner.apply_cancels([job_id])
        return response

    monkeypatch.setattr(runner.api, "clip_qc_upload", upload)
    monkeypatch.setattr(runner.api, "clip_qc_status", status)
    monkeypatch.setattr(runner, "_sleep", lambda seconds: None)
    return uploads


def qc_job(count=2):
    job = payload(count)
    job.update(clip_qc=True, clip_qc_max_attempts=4)
    return job


def test_qc_absent_keeps_existing_path(cfg, studio, runner):
    runner.run_job(payload())
    assert not studio.events("/fail")
    assert [e["event"] for e in studio.events("/progress") if e["event"] == "clip_done"] == ["clip_done", "clip_done"]


def test_qc_pass_attempt_one(cfg, studio, runner, monkeypatch):
    uploads = setup_qc(runner, monkeypatch, [{"state": "pass"}, {"state": "pass"}])
    runner.run_job(qc_job())
    assert [(i, a) for i, a, _, _ in uploads] == [(1, 1), (2, 1)]
    assert [e["media_id"] for e in studio.events("/progress") if e["event"] == "clip_done"] == ["clip-1", "clip-2"]


def test_qc_retry_repolishes_and_chains_accepted_seed(cfg, studio, runner, monkeypatch):
    uploads = setup_qc(runner, monkeypatch, [{"state": "pending"}, {"state": "pending"},
                                             {"state": "retry", "prompt": "P2"}, {"state": "pass"},
                                             {"state": "pass"}])
    runner.run_job(qc_job())
    assert uploads[1][2] == "P2" and uploads[1][3].name == "clip-1-a2.mp4"
    assert [e["media_id"] for e in studio.events("/progress") if e["event"] == "clip_done"] == ["clip-1-a2", "clip-2"]
    seed = cfg.work_dir / "jobs/1-g1/seed-2.jpg"
    assert seed.is_file()
    expected = cfg.work_dir / "jobs/1-g1/expected.jpg"
    runner.media.last_frame(cfg.work_dir / "jobs/1-g1/clip-1-a2.mp4", expected, cfg.seed_offset_ms)
    assert seed.read_bytes() == expected.read_bytes()


def test_qc_i2v_retries_reuse_seed_frame(cfg, studio, runner, monkeypatch):
    job = qc_job(2)
    job["clips"][0]["mode"] = "t2v"
    uploads = setup_qc(runner, monkeypatch, [{"state": "pass"}, {"state": "retry", "prompt": "P2"},
                                             {"state": "pass"}])
    frames = []
    original = runner._generate_clip

    def generate(*args, **kwargs):
        frames.append(kwargs["frame"])
        return original(*args, **kwargs)

    monkeypatch.setattr(runner, "_generate_clip", generate)
    runner.run_job(job)
    assert frames[1] == frames[2]
    assert uploads[1][3].name == "clip-2.mp4" and uploads[2][3].name == "clip-2-a2.mp4"


def test_qc_use_attempt(cfg, studio, runner, monkeypatch):
    responses = [{"state": "retry", "prompt": f"P{i}"} for i in (2, 3, 4)]
    responses.append({"state": "use_attempt", "use_attempt": 2})
    uploads = setup_qc(runner, monkeypatch, responses)
    runner.run_job(qc_job(1))
    assert uploads[-1][1] == 4
    assert [e["media_id"] for e in studio.events("/progress") if e["event"] == "clip_done"] == ["clip-1-a2"]


def test_qc_failed_fails_without_clip_done(cfg, studio, runner, monkeypatch):
    setup_qc(runner, monkeypatch, [{"state": "failed", "waiting_reason": "vision unavailable"}])
    runner.run_job(qc_job(1))
    assert studio.events("/fail")[0]["reason"] == "other"
    assert studio.events("/fail")[0]["message"] == "vision unavailable"
    assert not [e for e in studio.events("/progress") if e["event"] == "clip_done"]


def test_qc_cancel_while_polling(cfg, studio, runner, monkeypatch):
    setup_qc(runner, monkeypatch, [{"state": "pending"}], cancel=True)
    runner.run_job(qc_job(1))
    assert not studio.events("/fail")
    assert not [e for e in studio.events("/progress") if e["event"] == "clip_done"]


def test_qc_resume_uses_saved_attempt(cfg, studio, runner, monkeypatch):
    job = qc_job(1)
    state = JobState(cfg.work_dir, 1, 1)
    path = state.directory / "clip-1-a2.mp4"
    path.write_bytes(b"saved clip")
    state.data.update(project_id="project-1", profile="p1", qc={"1": {
        "attempt": 2, "prompts": {"1": "P1", "2": "P2"},
        "files": {"2": {"media_id": "saved-2", "path": str(path)}}, "uploaded": [2]
    }})
    state.save()
    job["flow_project_id"] = "project-1"
    uploads = setup_qc(runner, monkeypatch, [{"state": "pass"}])
    generated = []
    monkeypatch.setattr(runner, "_generate_clip", lambda *args, **kwargs: generated.append(1))
    runner.run_job(job)
    assert not generated and not uploads
    assert [e["media_id"] for e in studio.events("/progress") if e["event"] == "clip_done"] == ["saved-2"]


def test_qc_transient_status_keeps_polling(cfg, studio, runner, monkeypatch):
    uploads = setup_qc(runner, monkeypatch, [{"state": "pending"}, {"state": "pass"}])
    original = runner.api.clip_qc_status
    calls = [0]

    def status(*args):
        calls[0] += 1
        if calls[0] == 1:
            from gflow_worker.api import TransientApiError
            raise TransientApiError("temporary")
        return {"state": "pass"}

    monkeypatch.setattr(runner.api, "clip_qc_status", status)
    runner.run_job(qc_job(1))
    assert calls[0] == 2 and len(uploads) == 1


def test_qc_retry_beyond_max_attempts_fails(cfg, studio, runner, monkeypatch):
    setup_qc(runner, monkeypatch, [{"state": "retry", "prompt": "P2"}] * 4)
    runner.run_job(qc_job(1))
    assert studio.events("/fail")[0]["message"] == "clip QC asked for too many attempts"


@pytest.mark.parametrize("limit", [1, 2, 6, 10])
def test_qc_custom_attempt_limit(cfg, studio, runner, monkeypatch, limit):
    job = qc_job(1)
    job["clip_qc_max_attempts"] = limit
    responses = [{"state": "retry", "prompt": f"P{i}"} for i in range(2, limit + 1)]
    responses.append({"state": "use_attempt", "use_attempt": 1})
    uploads = setup_qc(runner, monkeypatch, responses)
    monkeypatch.setattr(runner.media, "thumbnail", lambda video, jpg: Path(jpg).write_bytes(b"dummy"))
    runner.run_job(job)
    assert [attempt for _, attempt, _, _ in uploads] == list(range(1, limit + 1))
    assert not studio.events("/fail")


def test_qc_custom_limit_rejects_extra_retry(cfg, studio, runner, monkeypatch):
    job = qc_job(1)
    job["clip_qc_max_attempts"] = 1
    uploads = setup_qc(runner, monkeypatch, [{"state": "retry", "prompt": "P2"}])
    runner.run_job(job)
    assert len(uploads) == 1
    assert studio.events("/fail")[0]["message"] == "clip QC asked for too many attempts"


def test_qc_unknown_state_fails(cfg, studio, runner, monkeypatch):
    setup_qc(runner, monkeypatch, [{"state": "weird"}])
    runner.run_job(qc_job(1))
    assert studio.events("/fail")[0]["reason"] == "other"
    assert "unknown state" in studio.events("/fail")[0]["message"]
    assert not [e for e in studio.events("/progress") if e["event"] == "clip_done"]
