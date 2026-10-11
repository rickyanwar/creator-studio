from dataclasses import dataclass
import json
import os
import re
import shlex
import signal
import subprocess


@dataclass
class GflowResult:
    ok: bool
    payload: dict
    exit_code: int
    error_class: str | None = None
    retryable: bool = False
    message: str = ""


def last_json(stdout):
    decoder = json.JSONDecoder()
    result = None
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
            result = value
        position = start + end
    if result is None:
        raise ValueError("gflow returned no JSON object")
    return result


def classify(result):
    if result.exit_code == 37 or result.error_class == "InsufficientCreditsError":
        return "out_of_credits"
    if result.exit_code in (8, 12, 14, 38, 39):
        return "auth"
    text = json.dumps([result.payload.get("failure_reasons"), result.payload.get("error_message")]).upper()
    if result.payload.get("status") == "fail" and re.search(r"SAFETY|UNSAFE|POLICY|BLOCKED|PROHIBITED|RAI", text):
        return "policy"
    if result.exit_code in (9, 10, 15, 23, 25, 28, 31, 36) or result.error_class == "TimeoutExpired":
        return "transient"
    return "other"


class GflowRunner:
    def __init__(self, cfg):
        self.cfg = cfg

    def run(self, args):
        # The studio token is never needed by the child CLI.
        env = {k: v for k, v in os.environ.items() if k != "GFLOW_WORKER_TOKEN"}
        try:
            process = subprocess.Popen([*shlex.split(self.cfg.gflow_cmd), *map(str, args), "--json"],
                                       cwd=self.cfg.cli_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       text=True, env=env, start_new_session=True)
            try:
                stdout, _ = process.communicate(timeout=self.cfg.clip_timeout_seconds)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.communicate()
                return GflowResult(False, {}, -1, "TimeoutExpired", True, "gflow timed out")
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
        return self.run(["project", "create", "--profile", profile, "--name", name])

    def _video(self, mode, prompt, *, profile, project, model, aspect, duration, out, frame=None):
        args = ["video", mode]
        if frame is not None:
            args += ["--initial-frame", str(frame)]
        return self.run([*args, prompt, "--model", model, "--aspect", aspect, "--duration", str(duration),
                         "--profile", profile, "--project", project, "-o", str(out)])

    def t2v(self, prompt, **kwargs):
        return self._video("t2v", prompt, **kwargs)

    def i2v(self, prompt, *, frame, **kwargs):
        return self._video("i2v", prompt, frame=frame, **kwargs)
