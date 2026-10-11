from concurrent.futures import Future
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import time

import httpx
import pytest

from gflow_worker.api import JobLost, StudioClient, TransientApiError, UploadTooLarge
from gflow_worker.config import Config
from gflow_worker.gflow import GflowResult, GflowRunner, classify, last_json
from gflow_worker.main import Worker
from gflow_worker.media import Media
from gflow_worker import memory
from gflow_worker.state import JobState, cleanup
from .conftest import calls, payload


@pytest.mark.parametrize("count", [2, 3])
def test_happy_path(cfg, studio, runner, count):
    runner.run_job(payload(count))
    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    upload = studio.uploads[0]
    assert upload["worker_id"] == "test-worker" and upload["generation"] == "1"
    meta = json.loads(upload["meta"])
    assert meta["duration_s"] == pytest.approx(count, abs=0.2)
    assert meta["clips"] == [{"index": i, "media_id": f"clip-{i}"} for i in range(1, count + 1)]
    commands = calls(cfg)
    assert commands[0][:2] == ["project", "create"]
    for i, command in enumerate(commands[1:], 1):
        assert command[:2] == ["video", "t2v" if i == 1 else "i2v"]
        assert command[command.index("--profile") + 1] == "p1"
        assert command[command.index("--project") + 1] == "project-1"
        assert command[-1] == "--json"
        if i > 1:
            frame = Path(command[command.index("--initial-frame") + 1])
            assert frame.name == f"seed-{i}.png" and frame.stat().st_size > 0
            # Verify the actual seed pixels are extracted from the preceding clip.
            expected = frame.with_name("expected.png")
            Media(cfg).last_frame(frame.with_name(f"clip-{i - 1}.mp4"), expected, cfg.seed_offset_ms)
            assert frame.read_bytes() == expected.read_bytes()
    assert JobState(cfg.work_dir, 1, 1).data["uploaded"] is True


@pytest.mark.parametrize("acknowledged", [True, False])
def test_resume_only_remaining(cfg, studio, runner, acknowledged):
    job = payload()
    state = JobState(cfg.work_dir, 1, 1)
    out = state.directory / "clip-1.mp4"
    result = runner.gflow.t2v("clip 1", profile="p1", project="project-1", model=job["model"], aspect="9:16", duration=1, out=out)
    state.data.update(project_id="project-1", clips={"1": {"media_id": result.payload["media_id"], "path": str(out)}})
    state.save()
    job["flow_project_id"] = "project-1"
    if acknowledged:
        job["clips_done"] = [{"index": 1, "media_id": "clip-1"}]
    runner.run_job(job)
    assert len(studio.uploads) == 1
    assert [c[1] for c in calls(cfg)] == ["t2v", "i2v"]
    assert [e["clip_index"] for e in studio.events("/progress") if e["event"] == "clip_done"] == ([2] if acknowledged else [1, 2])


@pytest.mark.parametrize("missing_file", [False, True])
def test_lost_checkpoint(cfg, studio, runner, missing_file):
    job = payload()
    job["clips_done"] = [{"index": 1, "media_id": "m1"}]
    if missing_file:
        state = JobState(cfg.work_dir, 1, 1)
        state.data["clips"]["1"] = {"media_id": "m1", "path": str(state.directory / "absent.mp4")}
        state.save()
    runner.run_job(job)
    assert studio.events("/fail")[0]["reason"] == "lost_checkpoint"
    assert calls(cfg) == []


@pytest.mark.parametrize("mode,reason", [("37", "out_of_credits"), ("9", "transient"), ("policy", "policy"), ("timeout", "transient"), ("invalid", "other")])
def test_fake_failures(cfg, monkeypatch, mode, reason):
    monkeypatch.setenv("FAKE_GF_MODE", mode)
    result = GflowRunner(replace(cfg, clip_timeout_seconds=0.15 if mode == "timeout" else 30)).credits_list()
    assert not result.ok
    assert classify(result) == reason


@pytest.mark.parametrize("code", [8, 12, 14, 38, 39])
def test_auth_classification(code):
    assert classify(GflowResult(False, {}, code)) == "auth"


@pytest.mark.parametrize("code", [9, 10, 15, 23, 25, 28, 31, 36])
def test_transient_classification(code):
    assert classify(GflowResult(False, {}, code)) == "transient"


@pytest.mark.parametrize("word", ["SAFETY", "UNSAFE", "POLICY", "BLOCKED", "PROHIBITED", "RAI"])
def test_policy_classification(word):
    assert classify(GflowResult(False, {"status": "fail", "error_message": word}, 1)) == "policy"


def test_class_by_name_and_json_parser():
    assert classify(GflowResult(False, {}, 1, "InsufficientCreditsError")) == "out_of_credits"
    assert classify(GflowResult(False, {}, 1)) == "other"
    assert last_json('log {bad}\n{"a": {"nested": 1}}\n{"status": "ok", "x": {"y": 2}}') == {"status": "ok", "x": {"y": 2}}


def test_cancel_before_generation(cfg, studio, runner):
    runner.apply_cancels([1])
    runner.run_job(payload())
    assert not calls(cfg) and not studio.requests


def test_cancel_after_first_clip(cfg, studio, runner):
    studio.on_progress = lambda data: runner.apply_cancels([1]) if data["event"] == "clip_done" else None
    runner.run_job(payload())
    assert len(calls(cfg)) == 2 and not studio.uploads and not studio.events("/fail")


@pytest.mark.parametrize("event", ["project", "clip_done"])
def test_job_lost(cfg, studio, runner, event):
    studio.conflict_event = event
    runner.run_job(payload())
    assert not studio.uploads and not studio.events("/fail")
    assert not runner.busy_profiles


def test_busy_profile(cfg, studio, runner):
    runner.busy_profiles.add("p1")
    runner.run_job(payload())
    assert studio.events("/fail")[0]["message"] == "profile busy"
    assert not calls(cfg) and "p1" in runner.busy_profiles


@pytest.mark.parametrize("available,running,expected", [(None, 0, True), (2736, 0, True), (2735, 0, False), (9000, 1, False)])
def test_memory_gate(cfg, monkeypatch, available, running, expected):
    monkeypatch.setattr(memory, "available_mb", lambda: available)
    assert memory.can_start(cfg, running) is expected


def test_mac_memory(monkeypatch):
    monkeypatch.setattr(memory.sys, "platform", "darwin")
    monkeypatch.setattr(memory.subprocess, "check_output", lambda *a, **kw: 'Mach Virtual Memory Statistics: (page size of 16384 bytes)\nPages free: 64.\nPages inactive: 64.\nPages speculative: 64.\nPages purgeable: 64.\n')
    assert memory.available_mb() == 4


def test_linux_memory(monkeypatch):
    monkeypatch.setattr(memory.sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", lambda self: 'MemTotal: 9999 kB\nMemAvailable: 4096 kB\n')
    assert memory.available_mb() == 4


def test_credits_skipped_busy(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(cfg, studio.api)
    future = Future()
    worker.jobs[1] = (payload(), future)
    try:
        worker.tick()
        assert not calls(cfg)
        assert studio.events("/heartbeat")[0]["running_job_ids"] == [1]
        assert studio.events("/progress")[0]["event"] == "alive"
    finally:
        worker.executor.shutdown()


def test_credits_filter_and_interval(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(replace(cfg, profiles=("p2",)), studio.api)
    try:
        worker.tick()
        worker.tick()
        assert len(calls(cfg)) == 2  # credits list plus the one-time probe project
        assert worker.accounts == [{"profile": "p2", "email": None, "authenticated": False, "credits": None, "error": "no access", "error_class": "FlowAccessUnavailableError"}]
    finally:
        worker.executor.shutdown()


def test_heartbeat_credits_only_after_refresh(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(cfg, studio.api)
    try:
        worker.tick()
        worker.tick()
        heartbeats = studio.events("/heartbeat")
        assert heartbeats[0]["accounts"][0]["credits"] == 100
        assert heartbeats[1]["accounts"][0]["credits"] is None
    finally:
        worker.executor.shutdown()


@pytest.mark.parametrize("mode,credits,error", [("credits", 638, None), ("unknown", None, "credits unreadable"),
                                                  ("none", None, "no access")])
def test_credit_probe_fallback_and_heartbeat(cfg, studio, monkeypatch, mode, credits, error):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    monkeypatch.setenv("FAKE_PROBE_MODE", mode)
    worker = Worker(replace(cfg, profiles=("p2",), probe_timeout_seconds=0.1), studio.api)
    try:
        worker.tick()
        account = studio.events("/heartbeat")[0]["accounts"][0]
        assert account["profile"] == "p2"
        assert account["credits"] == credits
        assert account["error"] == error
        if mode != "none":
            assert account["authenticated"] is True
    finally:
        worker.executor.shutdown()


def test_probe_project_resolution_and_token_isolation(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    env_file = cfg.work_dir / "probe-env.txt"
    monkeypatch.setenv("FAKE_PROBE_MODE", "credits")
    monkeypatch.setenv("FAKE_PROBE_ENV_FILE", str(env_file))
    monkeypatch.setenv("GFLOW_WORKER_TOKEN", "must-not-leak")
    worker = Worker(replace(cfg, profiles=("p2",), probe_projects={"p2": "env-project"}), studio.api)
    try:
        worker.tick()
        assert "project=env-project" in env_file.read_text()
        assert "must-not-leak" not in env_file.read_text()
        assert not any(command[:2] == ["project", "create"] for command in calls(cfg))
        assert worker.accounts[0]["credits"] == 638

        cached_worker = Worker(replace(cfg, profiles=("p2",)), studio.api)
        cached_worker.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        (cached_worker.cfg.work_dir / "probe_projects.json").write_text('{"p2": "cached-project"}')
        cached_worker.tick()
        assert not any(command[:2] == ["project", "create"] for command in calls(cfg)[2:])
        cached_worker.executor.shutdown()

        create_worker = Worker(replace(cfg, profiles=("p2",), work_dir=cfg.work_dir / "create"), studio.api)
        create_worker.tick()
        create_worker.tick()
        assert len([command for command in calls(cfg) if command[:2] == ["project", "create"]]) == 1
        create_worker.executor.shutdown()
    finally:
        worker.executor.shutdown()


def test_upload_size_reencoded_before_upload(cfg, studio, runner, monkeypatch):
    video = JobState(cfg.work_dir, 1, 1).directory / "video.mp4"
    original_stat = Path.stat
    reencoded = {"called": False}

    def fake_stat(path):
        result = original_stat(path)
        if path == video:
            size = 39 * 1024 * 1024 if reencoded["called"] else 41 * 1024 * 1024
            return type("Stat", (), {"st_size": size, "st_mode": result.st_mode})()
        return result

    def fake_reencode(path, duration):
        reencoded["called"] = True

    monkeypatch.setattr(Path, "stat", fake_stat)
    monkeypatch.setattr(runner.media, "reencode_for_upload", fake_reencode)
    runner.run_job(payload(2))
    assert reencoded["called"]
    assert len(studio.uploads) == 1


def test_upload_413_fails_distinctly(cfg, studio, runner, monkeypatch):
    def reject(request):
        if request.url.path.endswith("/complete"):
            return httpx.Response(413)
        return studio.handle(request)

    runner.api.client = httpx.Client(base_url=cfg.studio_api_url.rstrip("/") + "/",
                                     headers={"Authorization": f"Bearer {cfg.token}"},
                                     transport=httpx.MockTransport(reject))
    runner.run_job(payload())
    assert studio.events("/fail")[-1]["message"] == "upload rejected: too large"


def test_generic_failure_log_includes_exception_type(cfg, studio, runner, caplog, monkeypatch):
    monkeypatch.setattr(runner, "_run", lambda job: (_ for _ in ()).throw(ValueError("bad value")))
    with caplog.at_level("WARNING"):
        runner.run_job(payload())
    assert "ValueError" in caplog.text


def test_main_claim_and_cancel(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    studio.claims = [payload()]
    studio.heartbeat_reply["cancel_job_ids"] = [1]
    worker = Worker(cfg, studio.api)
    try:
        worker.tick()
        worker.jobs[1][1].result(timeout=10)
        assert len(calls(cfg)) == 2  # credits plus probe project; job cancelled before video CLI
        worker.stop.set()
        worker.tick()
        assert studio.events("/heartbeat")[-1]["running_job_ids"] == []
    finally:
        worker.executor.shutdown()


def test_cleanup_old_uploaded_only(cfg):
    for jid, uploaded, old in [(1, True, True), (2, False, True), (3, True, False)]:
        state = JobState(cfg.work_dir, jid, 1)
        state.data["uploaded"] = uploaded
        state.save()
        if old:
            os.utime(state.path, (time.time() - 4 * 86400,) * 2)
    cleanup(cfg.work_dir, 3)
    assert not (cfg.work_dir / "jobs/1-g1").exists()
    assert (cfg.work_dir / "jobs/2-g1").exists() and (cfg.work_dir / "jobs/3-g1").exists()
    assert not list(cfg.work_dir.glob("jobs/*/.state-*"))


@pytest.mark.parametrize("status,exception", [(409, JobLost), (500, TransientApiError), (503, TransientApiError), (401, httpx.HTTPStatusError)])
def test_api_errors(cfg, status, exception):
    api = StudioClient(cfg, transport=httpx.MockTransport(lambda r: httpx.Response(status)))
    with pytest.raises(exception):
        api.claim()
    api.close()


def test_api_network_error(cfg):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)
    api = StudioClient(cfg, transport=httpx.MockTransport(handler))
    with pytest.raises(TransientApiError):
        api.claim()
    api.close()


def test_config_env(cfg, monkeypatch):
    monkeypatch.setenv("STUDIO_API_URL", cfg.studio_api_url)
    monkeypatch.setenv("GFLOW_WORKER_TOKEN", cfg.token)
    monkeypatch.setenv("GFLOW_CLI_DIR", str(cfg.cli_dir))
    monkeypatch.setenv("GFLOW_PROFILES", " p1, p2, ")
    config = Config.from_env()
    assert config.profiles == ("p1", "p2") and config.token not in repr(config)
    monkeypatch.setenv("GFLOW_WORKER_ID", "bad id")
    with pytest.raises(ValueError):
        Config.from_env()


def test_missing_env(monkeypatch):
    monkeypatch.delenv("STUDIO_API_URL", raising=False)
    with pytest.raises(ValueError, match="STUDIO_API_URL"):
        Config.from_env()


def test_concat_silent_clip(cfg, runner):
    state = JobState(cfg.work_dir, 1, 1)
    video = state.directory / "silent.mp4"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    out = state.directory / "concat.mp4"
    runner.media.concat([video, video], out)
    probe = runner.media.probe(out)
    assert any(s["codec_type"] == "audio" for s in probe["streams"])
    assert float(probe["format"]["duration"]) == pytest.approx(2, abs=0.2)
