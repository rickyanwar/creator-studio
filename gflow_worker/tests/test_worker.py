from concurrent.futures import Future
from dataclasses import replace
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
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


def test_cut_style_clips_are_all_t2v(cfg, studio, runner):
    # Cut-style templates (e.g. health) mark every clip t2v: no frame chaining, no seed frames.
    job = payload(3)
    job["clips"] = [dict(clip, mode="t2v") for clip in job["clips"]]
    runner.run_job(job)
    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    commands = calls(cfg)[1:]
    assert [command[:2] for command in commands] == [["video", "t2v"]] * 3
    assert all("--initial-frame" not in command for command in commands)


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


@pytest.mark.parametrize(
    "msg,reason",
    [
        ("migrated host: ... most likely Flow's one-time upload-terms confirmation (host=migrated)", "upload_terms"),
        ("file too large", "other"),
    ],
)
def test_upload_terms_classification(msg, reason):
    assert classify(GflowResult(False, {}, 27, "MediaUploadRejectedError", False, msg)) == reason


def test_unusual_activity_classification():
    msg = "Flow refused the submit: PUBLIC_ERROR_UNUSUAL_ACTIVITY (gRPC 7)"
    assert classify(GflowResult(False, {}, 28, None, False, msg)) == "unusual_activity"
    assert classify(GflowResult(False, {"error_message": "PUBLIC_ERROR_UNUSUAL_ACTIVITY"}, 28)) == "unusual_activity"


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
        if worker._refresh_thread:
            worker._refresh_thread.join()
        worker.tick()
        assert len(calls(cfg)) == 1  # credits list only; probe does not create projects
        assert worker.accounts == [{"profile": "p2", "email": None, "authenticated": False, "credits": None, "error": "no access", "error_class": "FlowAccessUnavailableError"}]
    finally:
        worker.executor.shutdown()


def test_heartbeat_credits_only_after_refresh(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(cfg, studio.api)
    try:
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        worker.tick()
        first_tick = studio.events("/heartbeat")[-1]
        worker.tick()
        second_tick = studio.events("/heartbeat")[-1]
        assert first_tick["accounts"][0]["credits"] == 100
        assert second_tick["accounts"][0]["credits"] is None
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
        if worker._refresh_thread:
            worker._refresh_thread.join()
        worker.tick()
        account = studio.events("/heartbeat")[-1]["accounts"][0]  # the tick's own heartbeat
        assert account["profile"] == "p2"
        assert account["credits"] == credits
        assert account["error"] == error
        if mode != "none":
            assert account["authenticated"] is True
    finally:
        worker.executor.shutdown()


def test_extract_credits_from_text():
    from gflow_worker.probe_credits import extract_credits_from_text

    # Indonesian formats
    assert extract_credits_from_text("1.035 kredit Google Flow") == 1035
    assert extract_credits_from_text("1.050 kredit") == 1050
    assert extract_credits_from_text("Saldo: 50 kredit") == 50
    assert extract_credits_from_text("0 kredit") == 0

    # English formats
    assert extract_credits_from_text("1,035 credits") == 1035
    assert extract_credits_from_text("1,050 Google Flow credits") == 1050
    assert extract_credits_from_text("You have 250 credits left") == 250
    assert extract_credits_from_text("0 credits") == 0

    # Exclusions
    assert extract_credits_from_text("Refund credits will be processed in 3 days") is None
    assert extract_credits_from_text("Purchase additional credits here") is None
    assert extract_credits_from_text("Memuat...") is None
    assert extract_credits_from_text("") is None


def test_credit_probe_transient_failure_retains_balance(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    monkeypatch.setenv("FAKE_PROBE_MODE", "credits")
    worker = Worker(replace(cfg, profiles=("p2",), probe_timeout_seconds=0.1), studio.api)
    try:
        # Tick 1: Initial probe succeeds -> 638 credits
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert worker.accounts[0]["credits"] == 638
        assert worker.accounts[0]["error"] is None

        # Force a refresh tick with mode="unknown" (transient network glitch)
        worker.next_full_refresh = None
        worker.last_refresh = None
        monkeypatch.setenv("FAKE_PROBE_MODE", "unknown")
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        worker.tick()
        # Retains 638 credits; does NOT flip to unreadable on single glitch
        assert worker.accounts[0]["credits"] == 638
        assert worker.accounts[0]["error"] is None
        heartbeat_accounts = studio.events("/heartbeat")[-1]["accounts"]
        assert heartbeat_accounts[0]["credits"] == 638
        assert heartbeat_accounts[0]["error"] is None

        # Force another refresh tick with mode="unknown" (second consecutive failure)
        worker.next_full_refresh = None
        worker.last_refresh = None
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        # Now it transitions to unreadable after 2 consecutive failures
        assert worker.accounts[0]["credits"] is None
        assert worker.accounts[0]["error"] == "credits unreadable"

        # Next tick recovers with mode="credits"
        worker.next_full_refresh = None
        worker.last_refresh = None
        monkeypatch.setenv("FAKE_PROBE_MODE", "credits")
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert worker.accounts[0]["credits"] == 638
        assert worker.accounts[0]["error"] is None
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
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert "project=env-project" in env_file.read_text()
        assert "must-not-leak" not in env_file.read_text()
        assert not any(command[:2] == ["project", "create"] for command in calls(cfg))
        assert worker.accounts[0]["credits"] == 638

        (cfg.work_dir / "accounts_cache.json").unlink(missing_ok=True)
        cached_worker = Worker(replace(cfg, profiles=("p2",)), studio.api)
        cached_worker.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        (cached_worker.cfg.work_dir / "probe_projects.json").write_text('{"p2": "cached-project"}')
        cached_worker.tick()
        if cached_worker._refresh_thread:
            cached_worker._refresh_thread.join()
        assert "project=cached-project" in env_file.read_text()
        assert not any(command[:2] == ["project", "create"] for command in calls(cfg)[2:])
        cached_worker.executor.shutdown()

        none_worker = Worker(replace(cfg, profiles=("p2",), work_dir=cfg.work_dir / "create"), studio.api)
        none_worker.tick()
        if none_worker._refresh_thread:
            none_worker._refresh_thread.join()
        assert "project=(none)" in env_file.read_text()
        assert not any(command[:2] == ["project", "create"] for command in calls(cfg))
        none_worker.executor.shutdown()
    finally:
        worker.executor.shutdown()


def test_upload_size_reencoded_before_upload(cfg, studio, runner, monkeypatch):
    video = JobState(cfg.work_dir, 1, 1).directory / "video.mp4"
    original_stat = Path.stat
    reencoded = {"called": False}

    def fake_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path.name == "video.mp4":
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
        if worker._refresh_thread:
            worker._refresh_thread.join()
            worker.tick()
        worker.jobs[1][1].result(timeout=10)
        assert len(calls(cfg)) == 1  # credits only; probe project not created; job cancelled before video CLI
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
    monkeypatch.setenv("GFLOW_PROXY", "socks5://100.64.96.50:1080")
    config = Config.from_env()
    assert config.profiles == ("p1", "p2") and config.token not in repr(config)
    assert config.proxy == "socks5://100.64.96.50:1080"
    monkeypatch.setenv("GFLOW_WORKER_ID", "bad id")
    with pytest.raises(ValueError):
        Config.from_env()


def test_gflow_runner_passes_proxy_env(cfg, monkeypatch):
    from gflow_worker.config import Config
    from gflow_worker.gflow import GflowRunner

    passed_env = {}

    def fake_popen(cmd, **kwargs):
        passed_env.update(kwargs.get("env", {}))

        class FakeProc:
            pid = 99999
            returncode = 0

            def communicate(self, timeout=None):
                return '{"status": "ok"}', ""

        return FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    cfg_with_proxy = Config(
        studio_api_url="http://studio",
        token="tok",
        cli_dir=cfg.cli_dir,
        proxy="socks5://100.64.96.50:1080",
    )
    runner = GflowRunner(cfg_with_proxy)
    res = runner.run(["test"])
    assert res.ok
    assert passed_env.get("GFLOW_PROXY") == "socks5://100.64.96.50:1080"
    assert passed_env.get("ALL_PROXY") == "socks5://100.64.96.50:1080"


def test_gflow_runner_fails_closed_when_proxy_unreachable(cfg, monkeypatch):
    from gflow_worker.config import Config
    from gflow_worker.gflow import GflowRunner
    import gflow_worker.proxy_guard as pg

    # Simulate proxy is unreachable and auto-heal fails
    monkeypatch.setattr(pg, "is_proxy_reachable", lambda *a, **kw: False)
    monkeypatch.setattr(pg, "revive_phone_proxy", lambda *a, **kw: False)

    cfg_with_proxy = Config(
        studio_api_url="http://studio",
        token="tok",
        cli_dir=cfg.cli_dir,
        proxy="socks5://127.0.0.1:9999",
    )
    runner = GflowRunner(cfg_with_proxy)
    res = runner.run(["test"])
    assert not res.ok
    assert res.error_class == "ProxyUnreachableError"
    assert "refusing to launch" in res.message.lower() or "unreachable" in res.message.lower()


def _hold_lock(path, seconds):
    return subprocess.Popen([sys.executable, "-c", """
import fcntl, sys, time
with open(sys.argv[1], 'a+') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    time.sleep(float(sys.argv[2]))
""", str(path), str(seconds)])


def test_gflow_run_waits_for_shared_lock(cfg):
    lock = cfg.work_dir / "flow.lock"
    lock.parent.mkdir(parents=True)
    holder = _hold_lock(lock, 0.25)
    time.sleep(0.05)
    started = time.monotonic()
    result = GflowRunner(replace(cfg, lock_file=lock, lock_timeout_seconds=1)).credits_list()
    assert result.ok and time.monotonic() - started >= 0.15
    holder.wait(timeout=2)


def test_gflow_lock_timeout_is_transient(cfg):
    lock = cfg.work_dir / "flow.lock"
    lock.parent.mkdir(parents=True)
    holder = _hold_lock(lock, 0.5)
    time.sleep(0.05)
    result = GflowRunner(replace(cfg, lock_file=lock, lock_timeout_seconds=0.05)).credits_list()
    assert not result.ok and result.error_class == "TimeoutExpired" and classify(result) == "transient"
    assert calls(cfg) == []
    holder.wait(timeout=2)


def test_read_only_existing_lock_file_works(cfg):
    lock = cfg.work_dir / "flow.lock"
    lock.parent.mkdir(parents=True)
    lock.touch()
    lock.chmod(0o444)
    try:
        result = GflowRunner(replace(cfg, lock_file=lock)).credits_list()
        assert result.ok
    finally:
        lock.chmod(0o644)


def test_probe_waits_for_shared_lock(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    monkeypatch.setenv("FAKE_PROBE_MODE", "credits")
    lock = cfg.work_dir / "flow.lock"
    lock.parent.mkdir(parents=True)
    holder = _hold_lock(lock, 0.25)
    time.sleep(0.05)
    worker = Worker(replace(cfg, profiles=("p2",), lock_file=lock, lock_timeout_seconds=1), studio.api)
    try:
        started = time.monotonic()
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert worker.accounts[0]["credits"] == 638 and time.monotonic() - started >= 0.15
    finally:
        worker.executor.shutdown()
        holder.wait(timeout=2)


def test_empty_lock_path_does_not_lock(cfg):
    result = GflowRunner(replace(cfg, lock_file=None)).credits_list()
    assert result.ok


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


def test_concat_trims_tail_of_all_but_last_clip(cfg, runner):
    state = JobState(cfg.work_dir, 1, 1)
    video = state.directory / "clip.mp4"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    out = state.directory / "concat_trimmed.mp4"
    runner.media.concat([video, video, video], out, trim_tail_ms=100)
    probe = runner.media.probe(out)
    assert float(probe["format"]["duration"]) == pytest.approx(2.8, abs=0.1)


def test_concat_without_trim_keeps_full_length(cfg, runner):
    state = JobState(cfg.work_dir, 1, 1)
    video = state.directory / "clip.mp4"
    subprocess.run([cfg.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24",
                    "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    out = state.directory / "concat_full.mp4"
    runner.media.concat([video, video, video], out)
    probe = runner.media.probe(out)
    assert float(probe["format"]["duration"]) == pytest.approx(3.0, abs=0.1)


def test_concat_uses_hermes_encode_settings(cfg, runner, monkeypatch):
    monkeypatch.setattr(runner.media, "probe", lambda path: {
        "format": {"duration": "1"},
        "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
    })
    commands = []
    monkeypatch.setattr(runner.media, "_run", lambda args: commands.append(args))

    runner.media.concat(["clip.mp4"], "out.mp4")

    args = commands[0]
    assert args[args.index("-c:v") + 2:args.index("-c:v") + 4] == ["-crf", "18"]
    assert args[args.index("-c:a") + 2:args.index("-c:a") + 4] == ["-b:a", "192k"]
    assert args[args.index("-movflags") + 1:args.index("-movflags") + 2] == ["+faststart"]


def test_two_worker_shared_locks_concurrent_hermes_exclusive_blocks(cfg, tmp_path):
    from gflow_worker.lock import flow_lock, LockTimeout
    import concurrent.futures

    lock_file = tmp_path / "gflow-flow.lock"

    # 1. Two shared locks can be held simultaneously
    held_shared = threading.Event()
    release_shared = threading.Event()
    worker1_started = threading.Event()
    worker2_started = threading.Event()

    def run_worker1():
        with flow_lock(lock_file, 1.0, profile=None):
            worker1_started.set()
            release_shared.wait()

    def run_worker2():
        worker1_started.wait()
        with flow_lock(lock_file, 1.0, profile=None):
            worker2_started.set()
            release_shared.wait()

    t1 = threading.Thread(target=run_worker1)
    t2 = threading.Thread(target=run_worker2)
    t1.start()
    t2.start()

    assert worker1_started.wait(timeout=2.0)
    assert worker2_started.wait(timeout=2.0)

    # 2. While shared lock is held, Hermes LOCK_EX blocks and times out
    holder = _hold_lock(lock_file, 0.2)
    # Give child process a moment to attempt flock LOCK_EX
    time.sleep(0.05)
    # The holder should still be running because it's blocked on the shared lock
    assert holder.poll() is None

    # Release worker shared locks
    release_shared.set()
    t1.join(timeout=2.0)
    t2.join(timeout=2.0)
    holder.wait(timeout=2.0)

    # 3. While Hermes holds LOCK_EX, worker shared lock blocks
    hermes_holder = _hold_lock(lock_file, 0.3)
    time.sleep(0.05)
    started = time.monotonic()
    with flow_lock(lock_file, 1.0, profile=None):
        elapsed = time.monotonic() - started
        assert elapsed >= 0.15
    hermes_holder.wait(timeout=2.0)


def test_per_profile_lock_serialization_and_concurrency(cfg, tmp_path):
    from gflow_worker.lock import flow_lock, LockTimeout

    lock_file = tmp_path / "gflow-flow.lock"

    # Same profile serializes
    p1_first_acquired = threading.Event()
    p1_release_first = threading.Event()
    p1_second_acquired = threading.Event()

    def worker_p1_first():
        with flow_lock(lock_file, 1.0, profile="profA"):
            p1_first_acquired.set()
            p1_release_first.wait()

    def worker_p1_second():
        p1_first_acquired.wait()
        try:
            with flow_lock(lock_file, 0.1, profile="profA"):
                p1_second_acquired.set()
        except LockTimeout:
            pass

    t1 = threading.Thread(target=worker_p1_first)
    t2 = threading.Thread(target=worker_p1_second)
    t1.start()
    t2.start()

    assert p1_first_acquired.wait(timeout=2.0)
    # Second should fail / time out while first holds profA lock
    t2.join(timeout=2.0)
    assert not p1_second_acquired.is_set()

    p1_release_first.set()
    t1.join(timeout=2.0)

    # Different profiles run concurrently
    pA_acquired = threading.Event()
    pB_acquired = threading.Event()
    release_both = threading.Event()

    def worker_pA():
        with flow_lock(lock_file, 1.0, profile="profA"):
            pA_acquired.set()
            release_both.wait()

    def worker_pB():
        pA_acquired.wait()
        with flow_lock(lock_file, 1.0, profile="profB"):
            pB_acquired.set()
            release_both.wait()

    tA = threading.Thread(target=worker_pA)
    tB = threading.Thread(target=worker_pB)
    tA.start()
    tB.start()

    assert pA_acquired.wait(timeout=2.0)
    assert pB_acquired.wait(timeout=2.0)

    release_both.set()
    tA.join(timeout=2.0)
    tB.join(timeout=2.0)


def test_lock_order_and_cleanup_on_profile_timeout(cfg, tmp_path):
    from gflow_worker.lock import flow_lock, LockTimeout
    import fcntl

    lock_file = tmp_path / "gflow-flow.lock"
    prof_file = tmp_path / "gflow-flow.my_prof.lock"

    # Pre-acquire profile lock exclusively using another fd
    prof_file.parent.mkdir(parents=True, exist_ok=True)
    with prof_file.open("a+") as pf:
        fcntl.flock(pf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

        # Worker attempting to acquire global-then-profile should time out on profile
        # and release global shared lock
        with pytest.raises(LockTimeout):
            with flow_lock(lock_file, 0.05, profile="my_prof"):
                pass

        # Verify global lock was released by acquiring it exclusively
        with lock_file.open("a+") as gf:
            fcntl.flock(gf.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(gf.fileno(), fcntl.LOCK_UN)


def test_max_concurrent_clamping(monkeypatch, cfg):
    monkeypatch.setenv("STUDIO_API_URL", "https://studio.test")
    monkeypatch.setenv("GFLOW_WORKER_TOKEN", "tok")
    monkeypatch.setenv("GFLOW_CLI_DIR", str(cfg.cli_dir))

    monkeypatch.setenv("GFLOW_MAX_CONCURRENT", "3")
    c3 = Config.from_env()
    assert c3.max_concurrent == 2

    monkeypatch.setenv("GFLOW_MAX_CONCURRENT", "5")
    c5 = Config.from_env()
    assert c5.max_concurrent == 2

    monkeypatch.setenv("GFLOW_MAX_CONCURRENT", "1")
    c1 = Config.from_env()
    assert c1.max_concurrent == 1

    monkeypatch.setenv("GFLOW_MAX_CONCURRENT", "2")
    c2 = Config.from_env()
    assert c2.max_concurrent == 2

    monkeypatch.setenv("GFLOW_MAX_CONCURRENT", "0")
    with pytest.raises(ValueError, match="invalid worker limits"):
        Config.from_env()


def test_claim_skips_locally_busy_account_in_worker(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    job1 = payload()
    job1["id"] = 101
    job1["account"] = {"id": 1, "profile": "p1"}

    job2 = payload()
    job2["id"] = 102
    job2["account"] = {"id": 1, "profile": "p1"}

    studio.claims = [job2]
    # Set max_concurrent to 2
    worker = Worker(replace(cfg, max_concurrent=2), studio.api)
    try:
        # Before ticking, pretend job1 is currently running on worker
        mock_future = Future()
        worker.jobs[101] = (job1, mock_future)
        worker.runner.busy_profiles.add("p1")

        # Worker tick claims next job
        worker.tick()

        # Job 102 has profile p1 which is in worker.runner.busy_profiles; runner should fail it
        worker.jobs[102][1].result(timeout=2.0)
        fail_events = [data for route, data in studio.requests if route.endswith(f"/jobs/{job2['id']}/fail")]
        assert len(fail_events) == 1
        assert fail_events[0]["reason"] == "other"
        assert fail_events[0]["message"] == "profile busy"
    finally:
        worker.executor.shutdown(wait=False)


def test_credit_regex_patterns():
    credit_patterns = [
        re.compile(r"(\d[\d.,]*)\s*(kredit|credits?)\s*(google\s*flow)?", re.I),
        re.compile(r"(\d[\d.,]*)\s*google\s*flow\s*(kredit|credits?)", re.I),
    ]

    def extract_credits(text):
        for line in text.splitlines():
            for pat in credit_patterns:
                match = pat.search(line)
                if match and "refund" not in line.lower() and "additional" not in line.lower():
                    raw_val = match.group(1)
                    return int(re.sub(r"[.,]", "", raw_val))
        return None

    assert extract_credits("910 Google Flow credits") == 910
    assert extract_credits("1.050 kredit Google Flow") == 1050


def test_credits_list_fails_discovers_profiles_and_probes(cfg, studio, monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    monkeypatch.setenv("FAKE_GF_MODE", "37")  # credits_list fails
    monkeypatch.setenv("FAKE_PROBE_MODE", "credits")

    cli_home = tmp_path / "gflow-cli"
    cli_home.mkdir(parents=True)
    (cli_home / "profile_default").mkdir()
    (cli_home / "profile_life").mkdir()
    (cli_home / "profile_other").mkdir()
    (cli_home / "not_a_profile").mkdir()
    (cli_home / "profile_file.txt").touch()

    monkeypatch.setenv("GFLOW_CLI_HOME", str(cli_home))

    worker = Worker(replace(cfg, profiles=("life",)), studio.api)
    try:
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert len(worker.accounts) == 1
        acc = worker.accounts[0]
        assert acc["profile"] == "life"
        assert acc["authenticated"] is True
        assert acc["credits"] == 638
    finally:
        worker.executor.shutdown()


@pytest.fixture(autouse=True)
def _mock_proxy_guard(monkeypatch):
    import gflow_worker.proxy_guard as pg
    monkeypatch.setattr(pg, "is_proxy_reachable", lambda *a, **kw: True)


def test_submit_no_reply_marks_credits_stale(cfg, studio, runner, monkeypatch):
    msg = (
        "migrated host: no YhhmEf/eb1hJf/MZZa6b/nprQif reply within 60s of clicking submit "
        "(rpcs seen: YhhmEf)"
    )
    fake_result = GflowResult(ok=False, payload={}, exit_code=9, error_class="TransportError", retryable=True, message=msg)
    monkeypatch.setattr(runner.gflow, "t2v", lambda *a, **kw: fake_result)

    assert runner.credits_stale is False
    runner.run_job(payload())

    assert runner.credits_stale is True
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == classify(fake_result)
    assert fail_events[0]["reason"] == "transient"
    assert fail_events[0]["message"] == msg


def test_other_failure_keeps_credits_fresh(cfg, studio, runner, monkeypatch):
    msg = "some other network error"
    fake_result = GflowResult(ok=False, payload={}, exit_code=9, error_class="TransportError", retryable=True, message=msg)
    monkeypatch.setattr(runner.gflow, "t2v", lambda *a, **kw: fake_result)

    assert runner.credits_stale is False
    runner.run_job(payload())

    assert runner.credits_stale is False
    fail_events = studio.events("/fail")
    assert len(fail_events) == 1
    assert fail_events[0]["reason"] == classify(fake_result)
    assert fail_events[0]["reason"] == "transient"
    assert fail_events[0]["message"] == msg


def test_tick_refreshes_stale_credits_when_idle_only(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(cfg, studio.api)
    refresh_calls = []
    monkeypatch.setattr(worker, "_refresh_accounts", lambda: refresh_calls.append(True))

    worker.runner.credits_stale = True
    worker.last_refresh = time.monotonic()
    future = Future()
    worker.jobs[1] = (payload(), future)
    try:
        worker.tick()
        assert worker.runner.credits_stale is True
        assert len(refresh_calls) == 0

        worker.jobs.clear()
        worker.tick()
        assert worker.runner.credits_stale is False
        assert len(refresh_calls) == 1
    finally:
        worker.executor.shutdown()






def test_refresh_thread_heartbeat_and_phase(cfg, studio, monkeypatch):
    """While a slow refresh thread runs, tick() posts heartbeat with phase and skips claims; next tick after finish sends credits."""
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    monkeypatch.setenv("FAKE_PROBE_MODE", "credits")
    job = payload()
    job["account"]["profile"] = "p2"
    studio.claims = [job]
    worker = Worker(replace(cfg, profiles=("p2",), probe_timeout_seconds=0.1), studio.api)
    refresh_started = threading.Event()
    release_refresh = threading.Event()

    orig_probe = worker._probe_credits

    def slow_probe(account, previous=None):
        refresh_started.set()
        release_refresh.wait(timeout=5)
        orig_probe(account, previous=previous)

    monkeypatch.setattr(worker, "_probe_credits", slow_probe)
    try:
        # Tick 1: starts the slow refresh thread
        worker.tick()
        assert refresh_started.wait(timeout=2)

        # While refresh thread is running: tick() still sends heartbeat with phase and does not claim
        worker.tick()
        beats = studio.events("/heartbeat")
        assert len(beats) >= 2
        last_beat = beats[-1]
        assert last_beat["phase"] is not None
        assert last_beat["phase"]["kind"] == "checking_credits"
        assert all(a["credits"] is None for a in last_beat["accounts"])
        assert len(worker.jobs) == 0  # no claim while refresh thread is alive

        # Let the refresh thread complete
        release_refresh.set()
        worker._refresh_thread.join(timeout=3)

        # Next tick after thread finishes carries credits and phase is None, and can claim jobs
        worker.tick()
        beats = studio.events("/heartbeat")
        last_beat = beats[-1]
        assert last_beat["phase"] is None
        assert last_beat["accounts"][0]["credits"] == 638
        assert job["id"] in worker.jobs
    finally:
        release_refresh.set()
        if worker._refresh_thread:
            worker._refresh_thread.join(timeout=2)
        worker.executor.shutdown()


def test_no_reply_refreshes_only_the_failed_profile(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(cfg, studio.api)
    try:
        worker.tick()  # full refresh once
        if worker._refresh_thread:
            worker._refresh_thread.join()
        probed = []
        monkeypatch.setattr(worker, "_probe_credits", lambda account, previous=None: probed.append(account["profile"]))
        monkeypatch.setattr(worker, "_refresh_accounts", lambda: probed.append("ALL"))
        worker.last_profile_probe.clear()
        worker.runner.credits_stale = True
        worker.runner.stale_profiles = {worker.accounts[0]["profile"]}
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert probed == [worker.accounts[0]["profile"]]
        assert worker.runner.stale_profiles == set()
    finally:
        worker.executor.shutdown()


def test_next_full_refresh_scheduled_with_jitter(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    monkeypatch.setattr("random.uniform", lambda a, b: 0.0)
    worker = Worker(cfg, studio.api)
    try:
        t0 = time.monotonic()
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert worker.next_full_refresh is not None
        assert pytest.approx(worker.next_full_refresh - t0, abs=5) == 1440 * 60

        monkeypatch.setattr("random.uniform", lambda a, b: 120.0)
        worker.next_full_refresh = None
        t1 = time.monotonic()
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert pytest.approx(worker.next_full_refresh - t1, abs=5) == (1440 + 120) * 60
    finally:
        worker.executor.shutdown()


def test_accounts_cache_startup_and_stale(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    cfg.work_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cfg.work_dir / "accounts_cache.json"

    # Write a fresh cache (1 hour old, < 20h limit)
    cache_file.write_text(json.dumps({
        "at": time.time() - 3600,
        "accounts": [{"profile": "p1", "email": "p1@test.local", "authenticated": True, "credits": 500, "error": None, "error_class": None}]
    }))

    probed = []
    credits_list_called = []
    worker = Worker(cfg, studio.api)
    monkeypatch.setattr(worker.gflow, "credits_list", lambda: credits_list_called.append(True))
    monkeypatch.setattr(worker, "_probe_credits", lambda account, previous=None: probed.append(account["profile"]))

    try:
        worker.startup()
        worker.tick()
        # Fresh cache: accounts loaded, NO credits_list or _probe_credits called
        assert len(worker.accounts) == 1
        assert worker.accounts[0]["profile"] == "p1"
        assert worker.accounts[0]["credits"] == 500
        assert len(credits_list_called) == 0
        assert len(probed) == 0
        assert worker.next_full_refresh is not None

        # Write a stale cache (25 hours old, >= 20h limit)
        cache_file.write_text(json.dumps({
            "at": time.time() - 25 * 3600,
            "accounts": [{"profile": "p1", "email": "p1@test.local", "authenticated": True, "credits": 500, "error": None, "error_class": None}]
        }))
        stale_worker = Worker(cfg, studio.api)
        stale_probed = []
        monkeypatch.setattr(stale_worker, "_probe_credits", lambda account, previous=None: stale_probed.append(account["profile"]))
        stale_worker.startup()
        assert stale_worker.next_full_refresh is None
        stale_worker.tick()
        if stale_worker._refresh_thread:
            stale_worker._refresh_thread.join()
        assert len(stale_probed) >= 1
        stale_worker.executor.shutdown()
    finally:
        worker.executor.shutdown()


def test_second_no_reply_within_2h_skips_probe(cfg, studio, monkeypatch):
    monkeypatch.setattr(memory, "available_mb", lambda: 9000)
    worker = Worker(cfg, studio.api)
    try:
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        profile = worker.accounts[0]["profile"]

        probed = []
        monkeypatch.setattr(worker, "_probe_credits", lambda account, previous=None: probed.append(account["profile"]))

        # First stale profile refresh: probe happens
        worker.last_profile_probe.clear()
        worker.runner.credits_stale = True
        worker.runner.stale_profiles = {profile}
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert probed == [profile]

        # Second stale profile refresh within 2h: probe is skipped
        worker.runner.credits_stale = True
        worker.runner.stale_profiles = {profile}
        worker.tick()
        if worker._refresh_thread:
            worker._refresh_thread.join()
        assert probed == [profile]  # unchanged, not probed again
    finally:
        worker.executor.shutdown()


def test_moved_job_drops_old_account_project(cfg, studio, runner):
    state = JobState(cfg.work_dir, 1, 1)
    state.data["project_id"] = "proj-p1"
    state.data["profile"] = "p1"
    state.data["clips"] = {"1": {"media_id": "clip-old", "path": "/tmp/dummy.mp4"}}
    state.save()

    job = payload(2)
    job["id"] = 1
    job["generation"] = 1
    job["account"] = {"profile": "p2"}
    job["clips_done"] = []
    job["flow_project_id"] = None

    runner.run_job(job)
    assert not studio.events("/fail")
    assert len(studio.uploads) == 1
    commands = calls(cfg)
    assert commands[0][:2] == ["project", "create"]
    assert commands[0][commands[0].index("--profile") + 1] == "p2"
    for cmd in commands[1:]:
        assert "--project" in cmd
        assert cmd[cmd.index("--project") + 1] != "proj-p1"
