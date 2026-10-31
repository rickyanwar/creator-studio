from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import re
import threading
import time


class LockTimeout(Exception):
    """The Flow lock was not available before the configured deadline."""


_tls = threading.local()


def _open_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not os.access(path, os.W_OK):
        return path.open("rb")
    return path.open("a+")


def _acquire(file_obj, operation: int, deadline: float):
    while True:
        try:
            fcntl.flock(file_obj.fileno(), operation | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise LockTimeout from None
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))


@contextmanager
def flow_lock(path: Path | None, timeout: float, profile: str | None = None):
    if path is None:
        yield
        return

    locks = getattr(_tls, "locks", None)
    if locks is None:
        locks = {}
        _tls.locks = locks

    key = (str(path.resolve()), profile)
    if key in locks:
        locks[key] += 1
        try:
            yield
        finally:
            locks[key] -= 1
            if locks[key] == 0:
                del locks[key]
        return

    deadline = time.monotonic() + timeout
    global_file = _open_lock(path)
    try:
        _acquire(global_file, fcntl.LOCK_SH, deadline)
        profile_file = None
        try:
            if profile:
                safe_prof = re.sub(r"[^A-Za-z0-9._-]", "-", profile)
                prof_path = path.parent / f"gflow-flow.{safe_prof}.lock"
                profile_file = _open_lock(prof_path)
                _acquire(profile_file, fcntl.LOCK_EX, deadline)
            locks[key] = 1
            try:
                yield
            finally:
                locks[key] -= 1
                if locks[key] == 0:
                    del locks[key]
        finally:
            if profile_file is not None:
                try:
                    fcntl.flock(profile_file.fileno(), fcntl.LOCK_UN)
                finally:
                    profile_file.close()
    finally:
        try:
            fcntl.flock(global_file.fileno(), fcntl.LOCK_UN)
        finally:
            global_file.close()

