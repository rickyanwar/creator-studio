from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import time


class LockTimeout(Exception):
    """The shared Flow lock was not available before the configured deadline."""


@contextmanager
def flow_lock(path: Path | None, timeout: float):
    if path is None:
        yield
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not os.access(path, os.W_OK):
        lock_file = path.open("rb")
    else:
        lock_file = path.open("a+")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout from None
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    finally:
        lock_file.close()
