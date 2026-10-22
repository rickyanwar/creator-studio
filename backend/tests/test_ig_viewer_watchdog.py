import contextlib
import os
import signal
import threading
import time
from unittest.mock import MagicMock

import pytest

import app.services.ig_viewer_scraper as ig_viewer_scraper
from app.services.ig_viewer_scraper import (
    _BROWSER_LAUNCH,
    _PER_USER_BUDGET,
    ViewerSessionTimeout,
    _descendant_pids,
    _fetch_for_username,
    _is_browser_process,
    _kill_browser_processes,
    _session_deadline,
    _session_watchdog,
    _tier_gramsnap,
    fetch_recent_posts,
)


def _make_proc_entry(proc_dir, pid, comm, ppid, cmdline=""):
    pdir = proc_dir / str(pid)
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "stat").write_text(f"{pid} ({comm}) S {ppid} 0 0\n")
    if cmdline:
        (pdir / "cmdline").write_bytes(cmdline.encode("utf-8") if isinstance(cmdline, str) else cmdline)
    else:
        (pdir / "cmdline").write_bytes(b"")


def test_descendant_pids_walks_tree(tmp_path):
    proc_dir = tmp_path / "proc"
    _make_proc_entry(proc_dir, 100, "root", 1)
    _make_proc_entry(proc_dir, 101, "child1", 100)
    _make_proc_entry(proc_dir, 102, "grandchild", 101)
    _make_proc_entry(proc_dir, 103, "other", 1)
    res = _descendant_pids(100, str(proc_dir))
    assert set(res) == {101, 102}
    assert 103 not in res


def test_descendant_pids_handles_comm_with_spaces_and_parens(tmp_path):
    proc_dir = tmp_path / "proc"
    pdir = proc_dir / "200"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "stat").write_text("200 (chrome (renderer) x) S 100 0 0\n")
    res = _descendant_pids(100, str(proc_dir))
    assert res == [200]


def test_descendant_pids_missing_proc_dir_returns_empty(tmp_path):
    missing = tmp_path / "nonexistent_proc"
    assert _descendant_pids(100, str(missing)) == []


def test_is_browser_process_matches_markers(tmp_path):
    proc_dir = tmp_path / "proc"
    _make_proc_entry(proc_dir, 10, "chrome", 1, cmdline="/opt/google/chrome/chrome\0--type=renderer")
    _make_proc_entry(proc_dir, 20, "python3", 1, cmdline="/usr/bin/python3\0x")
    assert _is_browser_process(10, str(proc_dir)) is True
    assert _is_browser_process(20, str(proc_dir)) is False


def test_kill_browser_processes_only_kills_browsers(tmp_path, monkeypatch):
    proc_dir = tmp_path / "proc"
    _make_proc_entry(proc_dir, 100, "root", 1, "/usr/bin/python\0worker")
    _make_proc_entry(proc_dir, 101, "python", 100, "/usr/bin/python\0child")
    _make_proc_entry(proc_dir, 102, "python", 100, "/usr/bin/python\0other")
    _make_proc_entry(
        proc_dir,
        103,
        "node",
        101,
        "/usr/local/lib/python3.11/site-packages/patchright/driver/node\0.../cli.js\0run-driver",
    )
    _make_proc_entry(proc_dir, 104, "chrome", 103, "/opt/google/chrome/chrome\0--headless")
    _make_proc_entry(proc_dir, 105, "chrome", 104, "/opt/google/chrome/chrome\0--type=renderer")

    monkeypatch.setattr(ig_viewer_scraper.os, "getpid", lambda: 100)
    killed_signals = []
    monkeypatch.setattr(ig_viewer_scraper.os, "kill", lambda pid, sig: killed_signals.append((pid, sig)))

    killed_count = _kill_browser_processes(str(proc_dir))
    assert killed_count == 2
    assert killed_signals == [(104, signal.SIGKILL), (105, signal.SIGKILL)]


def test_session_watchdog_raises_timeout_after_kill(monkeypatch):
    monkeypatch.setattr(ig_viewer_scraper, "_kill_browser_processes", lambda proc_dir="/proc": 0)
    with pytest.raises(ViewerSessionTimeout) as exc_info:
        with _session_watchdog(1, "t"):
            time.sleep(1.5)
    assert "t exceeded 1s" in str(exc_info.value)


def test_session_watchdog_converts_inner_error_after_fire(monkeypatch):
    monkeypatch.setattr(ig_viewer_scraper, "_kill_browser_processes", lambda proc_dir="/proc": 0)
    with pytest.raises(ViewerSessionTimeout) as exc_info:
        with _session_watchdog(1, "t"):
            time.sleep(1.3)
            raise RuntimeError("pipe closed")
    assert isinstance(exc_info.value.__cause__, RuntimeError)
    assert str(exc_info.value.__cause__) == "pipe closed"


def test_session_watchdog_no_fire_passes_through():
    with _session_watchdog(5, "quick") as fired:
        pass
    assert not fired.is_set()

    with pytest.raises(ValueError, match="inner error"):
        with _session_watchdog(5, "quick"):
            raise ValueError("inner error")


def test_fetch_recent_posts_releases_lock_on_timeout(monkeypatch):
    fake_redis = MagicMock()
    monkeypatch.setattr(ig_viewer_scraper, "_acquire_lock", lambda *a, **kw: ("tok", fake_redis))
    release_calls = []
    monkeypatch.setattr(ig_viewer_scraper, "_release_lock", lambda tok, r: release_calls.append((tok, r)))
    monkeypatch.setattr(ig_viewer_scraper, "_launch_browser", lambda: (MagicMock(), MagicMock(), MagicMock()))

    @contextlib.contextmanager
    def _fake_watchdog(seconds, label):
        event = threading.Event()
        event.set()
        try:
            yield event
        except Exception as exc:
            raise ViewerSessionTimeout("fake timeout") from exc
        raise ViewerSessionTimeout("fake timeout")

    monkeypatch.setattr(ig_viewer_scraper, "_session_watchdog", _fake_watchdog)
    monkeypatch.setattr(ig_viewer_scraper, "_fetch_for_username", MagicMock(side_effect=RuntimeError("pipe")))

    with pytest.raises(ViewerSessionTimeout):
        fetch_recent_posts("user")

    assert len(release_calls) == 1
    assert release_calls[0] == ("tok", fake_redis)


def test_soft_time_limit_not_swallowed_by_tier(monkeypatch):
    class SoftTimeLimitExceeded(Exception):
        pass

    def _raising_tier_profile_viewer(*args, **kwargs):
        raise SoftTimeLimitExceeded("Celery timeout")

    monkeypatch.setattr(ig_viewer_scraper, "_tier_profile_viewer", _raising_tier_profile_viewer)
    with pytest.raises(SoftTimeLimitExceeded):
        _tier_gramsnap(MagicMock(), "u")

    monkeypatch.setattr(ig_viewer_scraper, "_record_result", lambda *a, **kw: None)
    fake_ctx = MagicMock()
    fake_ctx.new_page.return_value = MagicMock()
    with pytest.raises(SoftTimeLimitExceeded):
        _fetch_for_username(fake_ctx, "u", 12, [("x", _raising_tier_profile_viewer)])


def test_session_deadline_values():
    assert _session_deadline(1) == _BROWSER_LAUNCH + _PER_USER_BUDGET
    assert _session_deadline(5) == _BROWSER_LAUNCH + 5 * _PER_USER_BUDGET
