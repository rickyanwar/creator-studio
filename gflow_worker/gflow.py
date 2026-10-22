from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import time

from .lock import LockTimeout, flow_lock
from .proxy_guard import ensure_proxy_ready

SUCCESS = "MEDIA_GENERATION_STATUS_SUCCESSFUL"
FAILED = "MEDIA_GENERATION_STATUS_FAILED"


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
    detail = ((result.message or "") + json.dumps(result.payload.get("error") or {}) + " " + str(result.payload.get("error_message") or "")).upper()
    if result.error_class == "MediaUploadRejectedError" and "upload-terms" in (result.message or ""):
        return "upload_terms"
    if "UNUSUAL_ACTIVITY" in (result.message or "").upper() or "UNUSUAL_ACTIVITY" in text:
        return "unusual_activity"
    if re.search(r"BATAS PENGGUNAAN|USAGE LIMIT|REACHED YOUR (USAGE )?LIMIT|YOU'?VE HIT (THE |YOUR )?(USAGE )?LIMIT", detail):
        return "usage_limit"
    if result.exit_code == 37 or result.error_class == "InsufficientCreditsError":
        return "out_of_credits"
    if result.exit_code in (8, 12, 14, 38, 39):
        return "auth"
    if result.payload.get("status") == "fail" and re.search(r"SAFETY|UNSAFE|POLICY|BLOCKED|PROHIBITED|RAI", text):
        return "policy"
    # Flow's content refusals (PUBLIC_ERROR_UNSAFE_CONTENT/_FACE/_IDENTITY/_GENERATION) are caused by the prompt or seed
    # image, so retrying on other accounts can never succeed: report them as policy so the job and idea are skipped.
    if re.search(r"PUBLIC_ERROR_UNSAFE_[A-Z_]+", detail):
        return "policy"
    if "AGENT-ONLY COMPOSER" in detail or "COMPOSER IS AGENT-ONLY" in detail:
        return "agent_only"
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

    def wait_for_stranded_clip(self, *, profile, project, submit_time, timeout_s, poll_s, sleep=time.sleep, cancelled=lambda: False) -> str | None:
        try:
            elapsed = 0.0
            poll_step = max(float(poll_s), 1.0)
            while elapsed <= timeout_s:
                if cancelled():
                    return None
                try:
                    rows = self.list_videos(profile=profile, limit=20)
                except Exception:
                    rows = []
                candidates = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    if row.get("project_id") != project:
                        continue
                    if row.get("local_path"):
                        continue
                    if not row.get("created_at"):
                        continue
                    if submit_time:
                        try:
                            created = datetime.fromisoformat(row["created_at"])
                            if created.tzinfo is None and submit_time.tzinfo is not None:
                                created = created.replace(tzinfo=timezone.utc)
                            elif created.tzinfo is not None and submit_time.tzinfo is None:
                                created = created.astimezone(timezone.utc).replace(tzinfo=None)
                            if created < submit_time:
                                continue
                        except Exception:
                            continue
                    candidates.append(row)

                for row in candidates:
                    if row.get("status") in (SUCCESS, "SUCCESS", "SUCCESSFUL"):
                        return row.get("media_id")

                if candidates and all(row.get("status") in (FAILED, "FAILED") for row in candidates):
                    return None

                if elapsed + poll_step > timeout_s:
                    break

                sleep(poll_step)
                elapsed += poll_step
                if cancelled():
                    return None

            return None
        except Exception:
            return None
