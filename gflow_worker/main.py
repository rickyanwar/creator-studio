from concurrent.futures import ThreadPoolExecutor
import logging
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import sys
import threading
import time
import subprocess

from . import __version__, memory
from .api import JobLost, StudioClient, TransientApiError
from .config import Config
from .gflow import GflowRunner
from .lock import LockTimeout, flow_lock
from .media import Media
from .runner import JobRunner
from .state import cleanup

log = logging.getLogger(__name__)


class Worker:
    def __init__(self, cfg, api=None, gflow=None):
        self.cfg = cfg
        self.api = api or StudioClient(cfg)
        self.gflow = gflow or GflowRunner(cfg)
        self.runner = JobRunner(cfg, self.api, self.gflow, Media(cfg))
        self.accounts = []
        self._credits_heartbeat_pending = False
        self.last_refresh = None
        self.jobs = {}
        self.executor = ThreadPoolExecutor(max_workers=cfg.max_concurrent)
        self.stop = threading.Event()

    def startup(self):
        for executable in (self.cfg.ffmpeg, self.cfg.ffprobe):
            if shutil.which(executable) is None:
                raise ValueError("ffmpeg and ffprobe must be installed")
        cleanup(self.cfg.work_dir, self.cfg.keep_done_days)

    def _probe_project(self, profile):
        if profile in self.cfg.probe_projects:
            return self.cfg.probe_projects[profile]
        cache_path = self.cfg.work_dir / "probe_projects.json"
        try:
            cached = json.loads(cache_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            cached = {}
        if profile in cached:
            return cached[profile]
        result = self.gflow.create_project(profile, "credit-probe")
        project = result.payload.get("project_id") if result.ok else None
        if not project:
            return None
        self.cfg.work_dir.mkdir(parents=True, exist_ok=True)
        cached[profile] = project
        temporary = cache_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(cached, sort_keys=True))
        os.replace(temporary, cache_path)
        return project

    def _probe_credits(self, account):
        profile = account["profile"]
        project = self._probe_project(profile)
        if not project:
            return
        out_dir = self.cfg.work_dir / "probes" / re.sub(r"[^A-Za-z0-9._-]", "-", profile)
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        env = {key: value for key, value in os.environ.items() if key != "GFLOW_WORKER_TOKEN"}
        env["GFLOW_CLI_PROFILE"] = profile
        script = Path(__file__).with_name("probe_credits.py").resolve()
        command = [*shlex.split(self.cfg.probe_prefix), *shlex.split(self.cfg.probe_python_cmd),
                   str(script), project, str(out_dir)]
        try:
            with flow_lock(self.cfg.lock_file, self.cfg.lock_timeout_seconds):
                completed = subprocess.run(command, cwd=self.cfg.cli_dir, env=env, text=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           timeout=self.cfg.probe_timeout_seconds, check=False)
        except (LockTimeout, OSError, subprocess.TimeoutExpired):
            return
        output = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"^CREDITS:\s*(\d+)\s*$", output, re.MULTILINE)
        if match:
            account.update(authenticated=True, credits=int(match.group(1)), error=None, error_class=None)
        elif re.search(r"^CREDITS:\s*unknown\s*$", output, re.MULTILINE):
            account.update(authenticated=True, credits=None, error="credits unreadable", error_class=None)

    def _refresh_accounts(self):
        result = self.gflow.credits_list()
        profiles = result.payload.get("profiles", []) if isinstance(result.payload, dict) else []
        self.accounts = [dict(profile=p["profile"], email=p.get("email"),
                              authenticated=bool(p.get("authenticated")), credits=p.get("credits"),
                              error=p.get("error"), error_class=p.get("error_class") or p.get("error_type"))
                         for p in profiles if not self.cfg.profiles or p["profile"] in self.cfg.profiles]
        for account in self.accounts:
            if account["credits"] is None:
                self._probe_credits(account)
        if not result.ok:
            log.warning("Credits refresh failed (exit_code=%s, error_class=%s)",
                        result.exit_code, result.error_class)
        self._credits_heartbeat_pending = True

    def tick(self):
        self.jobs = {jid: entry for jid, entry in self.jobs.items() if not entry[1].done()}
        now = time.monotonic()
        if (self.last_refresh is None or now - self.last_refresh >= self.cfg.credits_refresh_minutes * 60) and memory.can_start(self.cfg, len(self.jobs)):
            # credits list inspects ALL profiles; avoid opening a second browser on a busy profile.
            if not self.jobs:
                self._refresh_accounts()
                self.last_refresh = now
        accounts = [dict(account, credits=account["credits"] if self._credits_heartbeat_pending else None)
                    for account in self.accounts]
        reply = self.api.heartbeat(dict(version=__version__, max_concurrent=self.cfg.max_concurrent,
                                         mem_available_mb=memory.available_mb(), running_job_ids=list(self.jobs), accounts=accounts))
        self._credits_heartbeat_pending = False
        self.runner.apply_cancels(reply.get("cancel_job_ids", []))
        for jid, (job, _) in self.jobs.items():
            if not self.runner.cancelled(jid):
                try:
                    self.api.progress(jid, job["generation"], "alive")
                except JobLost:
                    with self.runner.lock:
                        self.runner.cancel_ids.add(jid)
        while not self.stop.is_set() and reply.get("accept_jobs", False) and memory.can_start(self.cfg, len(self.jobs)):
            job = self.api.claim()
            if job is None:
                break
            if job["id"] in self.jobs:
                break
            if self.cfg.profiles and job["account"]["profile"] not in self.cfg.profiles:
                self.api.fail(job["id"], job["generation"], "other", "profile not configured on worker")
                break
            self.jobs[job["id"]] = (job, self.executor.submit(self.runner.run_job, job))

    def run(self):
        self.startup()
        try:
            while not self.stop.is_set():
                try:
                    self.tick()
                except TransientApiError:
                    log.warning("Studio temporarily unavailable")
                self.stop.wait(self.cfg.poll_seconds)
        finally:
            log.info("Waiting for running jobs to finish")
            self.executor.shutdown(wait=True)
            self.api.close()


def main():
    logging.basicConfig(level=logging.INFO, stream=sys.stdout, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = Config.from_env()
        worker = Worker(cfg)
        signals = 0

        def shutdown(signum, frame):
            nonlocal signals
            signals += 1
            if signals > 1:
                os._exit(128 + signum)
            worker.stop.set()

        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)
        worker.run()
    except Exception as exc:
        # HTTP errors can contain URLs; only log the exception type.
        log.error("Worker stopped (%s)", type(exc).__name__)
        raise SystemExit(1) from None
