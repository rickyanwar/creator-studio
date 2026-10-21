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
        return None

    def _probe_credits(self, account, previous=None):
        profile = account["profile"]
        project = self._probe_project(profile) or "(none)"
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
            with flow_lock(self.cfg.lock_file, self.cfg.lock_timeout_seconds, profile=profile):
                completed = subprocess.run(command, cwd=self.cfg.cli_dir, env=env, text=True,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           timeout=self.cfg.probe_timeout_seconds, check=False)
        except (LockTimeout, OSError, subprocess.TimeoutExpired):
            return
        output = f"{completed.stdout}\n{completed.stderr}"
        match = re.search(r"^CREDITS:\s*(\d+)\s*$", output, re.MULTILINE)
        if match:
            account.update(authenticated=True, credits=int(match.group(1)), error=None, error_class=None, _unreadable_count=0)
        elif re.search(r"^CREDITS:\s*unknown\s*$", output, re.MULTILINE):
            # If account previously had valid credits, tolerate a single transient probe failure
            # before marking it unreadable in heartbeat, avoiding false alarms during network lag.
            prev_credits = previous.get("credits") if previous else None
            unreadable_count = (previous.get("_unreadable_count", 0) + 1) if previous else 1
            if prev_credits is not None and unreadable_count < 2:
                account.update(authenticated=True, credits=prev_credits, error=None, error_class=None,
                               _unreadable_count=unreadable_count)
                log.warning("Probe for profile %s returned unreadable (transient attempt %d/2); retaining balance=%s",
                            profile, unreadable_count, prev_credits)
            else:
                account.update(authenticated=True, credits=None, error="credits unreadable", error_class=None,
                               _unreadable_count=unreadable_count)

    def _refresh_accounts(self):
        previous_by_profile = {a["profile"]: a for a in self.accounts}
        result = self.gflow.credits_list()
        profiles = result.payload.get("profiles", []) if isinstance(result.payload, dict) else []
        if profiles:
            self.accounts = [dict(profile=p["profile"], email=p.get("email"),
                                  authenticated=bool(p.get("authenticated")), credits=p.get("credits"),
                                  error=p.get("error"), error_class=p.get("error_class") or p.get("error_type"))
                             for p in profiles if not self.cfg.profiles or p["profile"] in self.cfg.profiles]
        else:
            cli_home = Path(os.getenv("GFLOW_CLI_HOME", "/home/gflow/.local/share/gflow-cli"))
            discovered = []
            if cli_home.is_dir():
                for entry in sorted(cli_home.iterdir()):
                    if entry.is_dir() and entry.name.startswith("profile_") and entry.name != "profile_default":
                        name = entry.name[len("profile_"):]
                        if not self.cfg.profiles or name in self.cfg.profiles:
                            discovered.append(name)
            self.accounts = [dict(profile=name, email=None, authenticated=False, credits=None,
                                  error=None, error_class=None)
                             for name in discovered]
        for account in self.accounts:
            if account["credits"] is None:
                self._probe_credits(account, previous=previous_by_profile.get(account["profile"]))
                self._alive_heartbeat()
        if not result.ok:
            log.warning("Credits refresh failed (exit_code=%s, error_class=%s)",
                        result.exit_code, result.error_class)
        self._credits_heartbeat_pending = True

    def _alive_heartbeat(self):
        """Keep the worker 'online' in Studio during long credit probes (one Chrome per account).
        Sends no credits (None = unchanged) and claims nothing."""
        try:
            self.api.heartbeat(dict(version=__version__, max_concurrent=self.cfg.max_concurrent,
                                    mem_available_mb=memory.available_mb(), running_job_ids=list(self.jobs),
                                    accounts=[dict(profile=a["profile"], email=a.get("email"),
                                                   authenticated=a.get("authenticated", False), credits=None,
                                                   error=a.get("error"), error_class=a.get("error_class"))
                                              for a in self.accounts]))
        except Exception as exc:  # noqa: BLE001 - a missed beat must never stop the probe loop
            log.warning("Alive heartbeat failed: %s", exc)

    def _refresh_stale_profiles(self):
        """After a no-reply submit, re-read only the accounts that failed instead of all of them."""
        with self.runner.lock:
            profiles, self.runner.stale_profiles = set(self.runner.stale_profiles), set()
        previous_by_profile = {a["profile"]: dict(a) for a in self.accounts}
        for account in self.accounts:
            if account["profile"] in profiles:
                account["credits"] = None
                self._probe_credits(account, previous=previous_by_profile.get(account["profile"]))
                self._alive_heartbeat()
        self._credits_heartbeat_pending = True

    def tick(self):
        self.jobs = {jid: entry for jid, entry in self.jobs.items() if not entry[1].done()}
        if self.runner.credits_stale and not self.jobs:
            self.runner.credits_stale = False
            if self.runner.stale_profiles and self.accounts:
                self._refresh_stale_profiles()  # only the accounts whose submit got no reply
            else:
                self.last_refresh = None  # unknown account: refresh all now
        now = time.monotonic()
        if (self.last_refresh is None or now - self.last_refresh >= self.cfg.credits_refresh_minutes * 60) and memory.can_start(self.cfg, len(self.jobs)):
            # credits list inspects ALL profiles; avoid opening a second browser on a busy profile.
            if not self.jobs:
                self._refresh_accounts()
                self.last_refresh = now
        accounts = [dict(profile=account["profile"], email=account.get("email"),
                         authenticated=account.get("authenticated", False),
                         credits=account["credits"] if self._credits_heartbeat_pending else None,
                         error=account.get("error"), error_class=account.get("error_class"))
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
