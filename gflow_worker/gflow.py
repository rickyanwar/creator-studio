from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess

from .lock import LockTimeout, flow_lock
from .proxy_guard import ensure_proxy_ready


@dataclass
class GflowResult:
    ok: bool
    payload: dict
    exit_code: int
    error_class: str | None = None
    retryable: bool = False
    message: str = ""


def all_json(stdout):
    decoder = json.JSONDecoder()
    results = []
    position = 0
    while position < len(stdout):
        start = stdout.find("{", position)
        if start < 0:
            break
        try:
            value, end = decoder.raw_decode(stdout[start:])
        except ValueError:
            position = start + 1
            continue
        if isinstance(value, dict):
            results.append(value)
        position = start + end
    return results


def last_json(stdout):
    results = all_json(stdout)
    if not results:
        raise ValueError("gflow returned no JSON object")
    return results[-1]


def classify(result):
    text = json.dumps([result.payload.get("failure_reasons"), result.payload.get("error_message")]).upper()
    if result.error_class == "MediaUploadRejectedError" and "upload-terms" in (result.message or ""):
        return "upload_terms"
    if "UNUSUAL_ACTIVITY" in (result.message or "").upper() or "UNUSUAL_ACTIVITY" in text:
        return "unusual_activity"
    if result.exit_code == 37 or result.error_class == "InsufficientCreditsError":
        return "out_of_credits"
    if result.exit_code in (8, 12, 14, 38, 39):
        return "auth"
    if result.payload.get("status") == "fail" and re.search(r"SAFETY|UNSAFE|POLICY|BLOCKED|PROHIBITED|RAI", text):
        return "policy"
    if result.exit_code in (9, 10, 15, 23, 25, 28, 31, 36) or result.error_class == "TimeoutExpired":
        return "transient"
    return "other"


class GflowRunner:
    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, args, profile=None):
        if self.cfg.proxy:
            try:
                ensure_proxy_ready(self.cfg.proxy)
            except RuntimeError as exc:
                return GflowResult(False, {}, -1, "ProxyUnreachableError", False, str(exc))
        # The studio token is never needed by the child CLI.
        env = {k: v for k, v in os.environ.items() if k != "GFLOW_WORKER_TOKEN"}
        if self.cfg.proxy:
            env["GFLOW_PROXY"] = self.cfg.proxy
            env["GFLOW_SOCKS5_PROXY"] = self.cfg.proxy
            env["ALL_PROXY"] = self.cfg.proxy
            env["HTTP_PROXY"] = self.cfg.proxy
            env["HTTPS_PROXY"] = self.cfg.proxy
        try:
            with flow_lock(self.cfg.lock_file, self.cfg.lock_timeout_seconds, profile=profile):
                process = subprocess.Popen([*shlex.split(self.cfg.gflow_cmd), *map(str, args), "--json"],
                                           cwd=self.cfg.cli_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, env=env, start_new_session=True)
                try:
                    stdout, _ = process.communicate(timeout=self.cfg.clip_timeout_seconds)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.communicate()
                    return GflowResult(False, {}, -1, "TimeoutExpired", True, "gflow timed out")
        except LockTimeout:
            return GflowResult(False, {}, -1, "TimeoutExpired", True, "gflow lock timed out")
        except OSError:
            return GflowResult(False, {}, -1, "ProcessError", False, "could not start gflow")
        try:
            payload = last_json(stdout)
        except ValueError as exc:
            return GflowResult(False, {}, process.returncode, "InvalidOutput", False, str(exc))
        error = payload.get("error") or {}
        if not isinstance(error, dict):
            error = {"detail": str(error)}
        return GflowResult(process.returncode == 0 and payload.get("status") not in ("fail", "error"),
                           payload, process.returncode, error.get("class"), bool(error.get("retryable", False)),
                           error.get("detail") or error.get("title") or payload.get("error_message") or "gflow failed")

    def credits_list(self):
        return self.run(["credits", "list"])

    def create_project(self, profile, name):
        return self.run(["project", "create", "--profile", profile, "--name", name], profile=profile)

    def _video(self, mode, prompt, *, profile, project, model, aspect, duration, out, frame=None):
        args = ["video", mode]
        if frame is not None:
            args += ["--initial-frame", str(frame)]
        return self.run([*args, prompt, "--model", model, "--aspect", aspect, "--duration", str(duration),
                         "--profile", profile, "--project", project, "-o", str(out)], profile=profile)

    def t2v(self, prompt, **kwargs):
        return self._video("t2v", prompt, **kwargs)

    def i2v(self, prompt, *, frame, **kwargs):
        return self._video("i2v", prompt, frame=frame, **kwargs)

    def t2i(self, prompt, *, profile, project, model, aspect, out):
        return self.run(["image", "t2i", prompt, "--model", model, "--aspect", aspect,
                         "--profile", profile, "--project", project, "-o", str(out)], profile=profile)

    def download_media(self, media_id, *, profile, out):
        out_dir = Path(out).parent if Path(out).suffix else Path(out)
        return self.run(["data", "download", str(media_id), "--profile", profile, "--out", str(out_dir)], profile=profile)

    def list_videos(self, *, profile, limit=20):
        if self.cfg.proxy:
            try:
                ensure_proxy_ready(self.cfg.proxy)
            except RuntimeError:
                return []
        env = {k: v for k, v in os.environ.items() if k != "GFLOW_WORKER_TOKEN"}
        if self.cfg.proxy:
            env["GFLOW_PROXY"] = self.cfg.proxy
            env["GFLOW_SOCKS5_PROXY"] = self.cfg.proxy
            env["ALL_PROXY"] = self.cfg.proxy
            env["HTTP_PROXY"] = self.cfg.proxy
            env["HTTPS_PROXY"] = self.cfg.proxy
        try:
            with flow_lock(self.cfg.lock_file, self.cfg.lock_timeout_seconds, profile=profile):
                process = subprocess.Popen([*shlex.split(self.cfg.gflow_cmd), "data", "list", "videos",
                                           "--profile", profile, "--limit", str(limit), "--json"],
                                           cwd=self.cfg.cli_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           text=True, env=env, start_new_session=True)
                stdout, _ = process.communicate(timeout=min(self.cfg.clip_timeout_seconds, 30.0))
        except Exception:
            return []
        return all_json(stdout)

    def find_stranded_media_id(self, *, profile, project=None, submit_time=None):
        try:
            rows = self.list_videos(profile=profile, limit=20)
        except Exception:
            return None
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Only a clip of THIS job's own project may be recovered; never guess across projects.
            if not project or row.get("project_id") != project:
                continue
            if not row.get("local_path") and (row.get("copy_count", 0) == 0 or row.get("status") in ("pending", "MEDIA_GENERATION_STATUS_FAILED")):
                if submit_time and row.get("created_at"):
                    try:
                        created = datetime.fromisoformat(row["created_at"])
                        if created < submit_time:
                            continue
                    except Exception:
                        pass
                return row.get("media_id")
        return None
