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

    def stranded_candidates(self, *, profile, project, submit_time) -> list[dict]:
        try:
            rows = self.list_videos(profile=profile, limit=20)
        except Exception:
            return []
        if not isinstance(rows, list):
            return []
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
            if row.get("status") in (FAILED, "FAILED"):
                continue
            try:
                raw_created = row["created_at"]
                if isinstance(raw_created, datetime):
                    created = raw_created
                else:
                    created = datetime.fromisoformat(str(raw_created))
            except Exception:
                continue
            if submit_time:
                try:
                    created_cmp = created
                    if created_cmp.tzinfo is None and submit_time.tzinfo is not None:
                        created_cmp = created_cmp.replace(tzinfo=timezone.utc)
                    elif created_cmp.tzinfo is not None and submit_time.tzinfo is None:
                        created_cmp = created_cmp.astimezone(timezone.utc).replace(tzinfo=None)
                    if created_cmp < submit_time:
                        continue
                except Exception:
                    continue
            candidates.append((created, row))

        def _sort_key(item):
            dt = item[0]
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        candidates.sort(key=_sort_key, reverse=True)
        return [row for _, row in candidates]

    def recover_from_project(self, *, profile, project, submit_time, out_dir, exclude=None):
        if self.cfg.proxy:
            try:
                ensure_proxy_ready(self.cfg.proxy)
            except RuntimeError as exc:
                return GflowResult(False, {}, -1, "ProxyUnreachableError", False, str(exc))
        env = {k: v for k, v in os.environ.items() if k != "GFLOW_WORKER_TOKEN"}
        env["GFLOW_CLI_PROFILE"] = profile
        if self.cfg.proxy:
            env["GFLOW_PROXY"] = self.cfg.proxy
            env["GFLOW_SOCKS5_PROXY"] = self.cfg.proxy
            env["ALL_PROXY"] = self.cfg.proxy
            env["HTTP_PROXY"] = self.cfg.proxy
            env["HTTPS_PROXY"] = self.cfg.proxy

        if isinstance(submit_time, datetime):
            epoch = int(submit_time.timestamp())
        else:
            try:
                epoch = int(float(submit_time))
            except Exception:
                epoch = 0

        exclude_args = [str(m) for m in (exclude or []) if str(m).strip()]
        script = Path(__file__).with_name("project_recover.py").resolve()
        command = [
            *shlex.split(self.cfg.probe_prefix),
            *shlex.split(self.cfg.probe_python_cmd),
            str(script),
            str(profile),
            str(project),
            str(epoch),
            str(out_dir),
            *exclude_args,
        ]
        try:
            with flow_lock(self.cfg.lock_file, self.cfg.lock_timeout_seconds, profile=profile):
                completed = subprocess.run(
                    command,
                    cwd=self.cfg.cli_dir,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=240,
                    check=False,
                )
        except Exception as exc:
            return GflowResult(False, {}, -1, "ProcessError", False, str(exc))

        stdout = (completed.stdout or "").strip()
        match = re.search(r"^RECOVERED\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)", stdout, re.MULTILINE)
        if match:
            media_id = match.group(1)
            workflow_id = match.group(2)
            local_path = match.group(3)
            size_bytes = int(match.group(4))
            payload = {
                "media_id": media_id,
                "workflow_id": workflow_id,
                "local_path": local_path,
                "bytes": size_bytes,
            }
            return GflowResult(True, payload, 0, None, False, "recovered from project")

        none_match = re.search(r"^NONE(?:\s+(.*))?$", stdout, re.MULTILINE)
        reason = none_match.group(1) if none_match else (stdout or "no clip recovered")
        return GflowResult(False, {}, completed.returncode, "RecoveryFailed", False, reason or "no clip recovered")

