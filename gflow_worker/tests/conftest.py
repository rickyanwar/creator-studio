from dataclasses import replace
import json
from pathlib import Path
import shlex
import shutil
import sys

import httpx
import pytest

from gflow_worker.api import StudioClient
from gflow_worker.config import Config
from gflow_worker.gflow import GflowRunner
from gflow_worker.media import Media
from gflow_worker.runner import JobRunner


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    ffmpeg_bin = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
    ffprobe_bin = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
    monkeypatch.setenv("FAKE_GF_LOG", str(tmp_path / "calls.jsonl"))
    monkeypatch.setenv("FAKE_FFMPEG", ffmpeg_bin)
    monkeypatch.delenv("FAKE_GF_MODE", raising=False)
    return Config("https://studio.test/api", "secret-test-token", tmp_path, worker_id="test-worker",
                   gflow_cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).with_name('fake_gflow.py')))}",
                   probe_python_cmd=f"{shlex.quote(sys.executable)} {shlex.quote(str(Path(__file__).with_name('fake_probe.py')))}",
                   work_dir=tmp_path / "work", ffmpeg=ffmpeg_bin, ffprobe=ffprobe_bin)


def payload(clips=2):
    return dict(id=1, generation=1, fanpage_id=5, template_key="test", title="Test", model="omni-flash", aspect="9:16",
                clip_seconds=1, clips=[dict(index=i, mode="t2v" if i == 1 else "i2v", prompt=f"clip {i}") for i in range(1, clips + 1)],
                clips_done=[], flow_project_id=None, project_name="ai-video-1-g1", account={"id": 1, "profile": "p1"}, lease_seconds=1200)


class Studio:
    def __init__(self, cfg):
        self.requests = []
        self.uploads = []
        self.conflict_event = None
        self.claims = []
        self.heartbeat_reply = {"accept_jobs": True, "cancel_job_ids": [], "credit_reserve": 0}
        self.on_progress = None
        self.api = StudioClient(cfg, transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        assert request.headers["authorization"] == "Bearer secret-test-token"
        route = request.url.path
        assert route.startswith("/api/worker/gflow/")
        if route.endswith("/complete"):
            body = request.read()
            assert b"video/mp4" in body and b"image/jpeg" in body
            boundary = request.headers["content-type"].split("boundary=")[1].encode()
            fields = {}
            for part in body.split(b"--" + boundary):
                if b"\r\n\r\n" not in part:
                    continue
                headers, value = part.split(b"\r\n\r\n", 1)
                if b'filename=' not in headers:
                    name = headers.split(b'name="')[1].split(b'"')[0].decode()
                    fields[name] = value.rstrip(b"\r\n").decode()
            self.uploads.append(fields)
            return httpx.Response(200, json={"status": "uploaded"})
        data = json.loads(request.read())
        self.requests.append((route, data))
        if route.endswith("/heartbeat"):
            return httpx.Response(200, json=self.heartbeat_reply)
        if route.endswith("/claim"):
            return httpx.Response(200, json={"job": self.claims.pop(0)}) if self.claims else httpx.Response(204)
        if route.endswith("/progress"):
            if data["event"] == self.conflict_event:
                return httpx.Response(409)
            if self.on_progress:
                self.on_progress(data)
        return httpx.Response(200, json={"ok": True, "status": "queued", "lease_seconds": 1200})

    def events(self, suffix):
        return [data for route, data in self.requests if route.endswith(suffix)]


@pytest.fixture
def studio(cfg):
    value = Studio(cfg)
    yield value
    value.api.close()


@pytest.fixture
def runner(cfg, studio):
    return JobRunner(cfg, studio.api, GflowRunner(cfg), Media(cfg))


def calls(cfg):
    path = cfg.cli_dir / "calls.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
