"""Ordered PIC message transport through the local Hermes CLI.

Bundles with a bundle_id are crash-safe and resumable (owner rule: never resend a message whose result is uncertain):
- The first request stores an immutable snapshot (target + ordered messages); retries replay that snapshot.
- Before Hermes is called for message i, "in_flight": i is written (fsync). Success moves i to "sent".
- A bundle whose state still has an unresolved in_flight index never sends again: the answer is "uncertain".
"""

import fcntl
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import urlopen

from phone_runner.recovery import get_hermes_path, hermes_command, sanitize_error

logger = logging.getLogger("phone_runner")

MAX_MEDIA_BYTES = 60 * 1024 * 1024
TARGET = re.compile(r"(whatsapp:\+?[0-9]{8,20}|telegram:(@[A-Za-z0-9_]{5,32}|-?[0-9]{5,20}))")
BUNDLE_ID = re.compile(r"[A-Za-z0-9:_-]{1,80}")
DEFAULT_STATE_DIR = "/var/lib/phone-runner/pic-state"
# Total request budget, counted from request entry: lock waits, downloads and Hermes calls all fit inside it.
# The backend client timeout is this budget + 30 s (backend/app/services/phone_runner_client.py).
BUNDLE_BASE_S = 100.0
BUNDLE_PER_EXTRA_MEDIA_S = 15.0
HERMES_CALL_MAX_S = 20.0
MESSAGE_GAP_S = 1.5
LOCK_POLL_S = 0.05
# State files are pruned after 7 days (always far above the 1 h minimum), and never while their bundle is locked.
STATE_MAX_AGE_S = 7 * 86400


def validate_request(target, messages, bundle_id=None) -> str | None:
    if bundle_id is not None and (not isinstance(bundle_id, str) or not BUNDLE_ID.fullmatch(bundle_id)):
        return "invalid_bundle_id"
    if not isinstance(target, str) or not TARGET.fullmatch(target):
        return "invalid_target"
    if not isinstance(messages, list) or not 1 <= len(messages) <= 16:
        return "invalid_messages"
    for message in messages:
        if not isinstance(message, dict):
            return "invalid_messages"
        if message.get("kind") == "text":
            text = message.get("text")
            if not isinstance(text, str) or len(text) > 4000:
                return "invalid_messages"
        elif message.get("kind") == "media":
            url = message.get("media_url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")):
                return "invalid_messages"
        else:
            return "invalid_messages"
    return None


def bundle_budget_s(messages) -> float:
    media = sum(message.get("kind") == "media" for message in messages)
    return BUNDLE_BASE_S + BUNDLE_PER_EXTRA_MEDIA_S * max(0, media - 1)


def download_media(url: str, path: str, *, deadline=None) -> None:
    timeout = min(30, max(0.001, deadline - time.monotonic())) if deadline is not None else 30
    with urlopen(url, timeout=timeout) as response, open(path, "wb") as output:
        size = 0
        while chunk := response.read(64 * 1024):
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("bundle timeout")
            size += len(chunk)
            if size > MAX_MEDIA_BYTES:
                raise ValueError("media_too_large")
            output.write(chunk)


def send_pic_messages(target, messages, subprocess_runner=None, downloader=None, *, bundle_id=None):
    error = validate_request(target, messages, bundle_id)
    if error:
        if bundle_id is not None:
            return {"ok": False, "detail": error, "sent": [], "resumed_from": 1}
        return False, error
    deadline = time.monotonic() + bundle_budget_s(messages)
    if bundle_id is None:
        return _send_messages(target, messages, subprocess_runner, downloader, deadline=deadline)
    return _send_bundle(target, messages, subprocess_runner, downloader, bundle_id, deadline)


def _try_lock(fd) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except BlockingIOError:
        return False


def _wait_lock(fd, deadline) -> bool:
    while not _try_lock(fd):
        if time.monotonic() >= deadline:
            return False
        time.sleep(LOCK_POLL_S)
    return True


def _lock_bundle(directory: Path, bundle_id: str, deadline):
    """Return a locked fd for this bundle, or None when the deadline passes first.

    Lock files are opened and unlinked only under the short directory lock, so pruning can never delete a lock
    file that another request is about to lock.
    """
    while True:
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            if not _wait_lock(dir_fd, deadline):
                return None
            fd = os.open(directory / f"{bundle_id}.lock", os.O_RDWR | os.O_CREAT, 0o600)
            if _try_lock(fd):
                return fd
            os.close(fd)
        finally:
            os.close(dir_fd)
        if time.monotonic() >= deadline:
            return None
        time.sleep(LOCK_POLL_S)


def _prune(directory: Path, deadline) -> None:
    """Delete bundle state older than STATE_MAX_AGE_S, skipping bundles another request holds locked."""
    dir_fd = os.open(directory, os.O_RDONLY)
    try:
        if not _wait_lock(dir_fd, deadline):
            return
        cutoff = time.time() - STATE_MAX_AGE_S
        for path in directory.glob("*.json"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                lock_path = path.with_suffix(".lock")
                fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    if not _try_lock(fd):
                        continue
                    path.unlink(missing_ok=True)
                    lock_path.unlink(missing_ok=True)
                finally:
                    os.close(fd)
            except OSError as exc:
                logger.warning("PIC state prune skipped %s: %s", path.name, type(exc).__name__)
    finally:
        os.close(dir_fd)


class _BundleProgress:
    """Durable per-bundle state. Every change is written to a temp file, fsynced and renamed into place."""

    def __init__(self, path: Path, state: dict):
        self.path = path
        self.state = state

    @property
    def sent(self) -> list[int]:
        return self.state["sent"]

    @property
    def in_flight(self):
        return self.state["in_flight"]

    def begin(self, index: int) -> None:
        self._write({**self.state, "in_flight": index})

    def done(self, index: int) -> None:
        self._write({**self.state, "sent": sorted(set(self.state["sent"]) | {index}), "in_flight": None})

    def not_started(self, index: int) -> None:
        if self.state["in_flight"] == index:
            self._write({**self.state, "in_flight": None})

    def _write(self, state: dict) -> None:
        temporary = self.path.with_suffix(".tmp")
        with temporary.open("w") as output:
            json.dump(state, output)
            output.flush()
            os.fsync(output.fileno())
        temporary.replace(self.path)
        dir_fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
        self.state = state


def _load_progress(path: Path, bundle_id: str, target, messages) -> _BundleProgress:
    if not path.exists():
        progress = _BundleProgress(path, {"target": target, "messages": messages, "sent": [], "in_flight": None})
        progress._write(progress.state)
        return progress
    state = json.loads(path.read_text())
    if not isinstance(state, dict) or validate_request(state.get("target"), state.get("messages")) is not None:
        raise ValueError("invalid_bundle_state")
    count = len(state["messages"])
    sent, in_flight = state.get("sent"), state.get("in_flight")
    if not isinstance(sent, list) or any(type(i) is not int or not 1 <= i <= count for i in sent):
        raise ValueError("invalid_bundle_state")
    if in_flight is not None and (type(in_flight) is not int or not 1 <= in_flight <= count):
        raise ValueError("invalid_bundle_state")
    if [state["target"], state["messages"]] != [target, messages]:
        logger.warning("PIC bundle %s: request payload differs from the stored snapshot; replaying the snapshot",
                       bundle_id)
    return _BundleProgress(path, {"target": state["target"], "messages": state["messages"],
                                  "sent": sorted(set(sent)), "in_flight": in_flight})


def _resumed_from(progress) -> int:
    count = len(progress.state["messages"])
    return next((i for i in range(1, count + 1) if i not in progress.sent), count + 1)


def _uncertain(progress, resumed_from: int) -> dict:
    index = progress.in_flight
    return {"ok": False, "uncertain": True, "in_flight": index,
            "detail": f"message {index}: delivery uncertain; not resent", "sent": list(progress.sent),
            "resumed_from": resumed_from}


def _send_bundle(target, messages, subprocess_runner, downloader, bundle_id, deadline):
    progress = None
    resumed_from = 1
    try:
        directory = Path(os.environ.get("PIC_STATE_DIR", DEFAULT_STATE_DIR))
        directory.mkdir(parents=True, exist_ok=True)
        lock_fd = _lock_bundle(directory, bundle_id, deadline)
        if lock_fd is None:
            return {"ok": False, "detail": "bundle_busy", "sent": [], "resumed_from": 1}
        try:
            _prune(directory, deadline)
            progress = _load_progress(directory / f"{bundle_id}.json", bundle_id, target, messages)
            resumed_from = _resumed_from(progress)
            if progress.in_flight is not None:
                logger.warning("PIC bundle %s: message %s has an uncertain result; nothing is sent",
                               bundle_id, progress.in_flight)
                return _uncertain(progress, resumed_from)
            ok, detail = _send_messages(progress.state["target"], progress.state["messages"], subprocess_runner,
                                        downloader, deadline=deadline, progress=progress)
        finally:
            os.close(lock_fd)
    except Exception as exc:
        ok, detail = False, sanitize_error(str(exc))
    if progress is not None and progress.in_flight is not None:
        return _uncertain(progress, resumed_from)
    sent = list(progress.sent) if progress is not None else []
    return {"ok": ok, "detail": detail, "sent": sent, "resumed_from": resumed_from}


def _send_messages(target, messages, subprocess_runner, downloader, *, deadline, progress=None):
    runner = subprocess_runner or subprocess.run
    download = downloader or download_media
    temp_dir = None
    try:
        for i, message in enumerate(messages, 1):
            if progress is not None and i in progress.sent:
                continue
            if time.monotonic() >= deadline:
                return False, "bundle timeout"
            try:
                if message["kind"] == "media":
                    if temp_dir is None:
                        temp_dir = tempfile.mkdtemp(prefix="pic-send-")
                    extension = Path(urlsplit(message["media_url"]).path).suffix or ".jpg"
                    path = str(Path(temp_dir) / f"media-{i}{extension}")
                    if downloader is None:
                        download(message["media_url"], path, deadline=deadline)
                    else:
                        download(message["media_url"], path)
                    if Path(path).stat().st_size > MAX_MEDIA_BYTES:
                        raise ValueError("media_too_large")
                    text = f"MEDIA:{path}"
                else:
                    text = re.sub("MEDIA:", "MEDIA :", message["text"], flags=re.IGNORECASE)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False, "bundle timeout"
                timeout = min(HERMES_CALL_MAX_S, remaining)
                if progress is not None:
                    progress.begin(i)  # durable before Hermes runs: a crash from here on is "uncertain"
                proc = runner(
                    hermes_command(["send", "-t", target, "-q", "--", text], timeout),
                    capture_output=True, text=True, timeout=timeout, check=False, shell=False,
                )
                if proc.returncode != 0:
                    # Hermes may have delivered before failing: the in_flight mark stays (uncertain, never resent).
                    error = proc.stderr or proc.stdout or f"exit code {proc.returncode}"
                    return False, f"message {i}: {sanitize_error(error)}"
                if progress is not None:
                    progress.done(i)
                if time.monotonic() >= deadline:
                    return False, "bundle timeout"
            except FileNotFoundError:
                # Hermes could not start (or the media file is missing): nothing was delivered.
                if progress is not None:
                    progress.not_started(i)
                return False, f"message {i}: {sanitize_error('executable or media not found: ' + get_hermes_path())}"
            except Exception as exc:
                if time.monotonic() >= deadline:
                    return False, "bundle timeout"
                return False, f"message {i}: {sanitize_error(str(exc))}"
            if i < len(messages):
                time.sleep(min(MESSAGE_GAP_S, max(0, deadline - time.monotonic())))
        return True, "sent"
    finally:
        if temp_dir is not None:
            shutil.rmtree(temp_dir)
