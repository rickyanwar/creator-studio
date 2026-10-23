"""HTTP server + asyncio worker for gemini-web-serve."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import random
import re
import signal
import socket
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from scheduler import (
    SchedulerConfig, after_use, apply_error, is_available, next_ready_at,
    pick_account, plan_day, _tz, _local_day,
)
from accounts import (
    account_status, default_account_state, list_account_files,
    load_account_file, load_state, merge_cookies_from_jar, save_account_file,
    save_state,
)

log = logging.getLogger("gws.server")

_MODEL_RE = re.compile(r"^[a-z0-9._-]{1,64}$")
_MAX_BODY = 256 * 1024

# Error type mapping by exception class name
_ERROR_MAP: dict[str, str] = {
    "AuthError": "auth",
    "UsageLimitExceededError": "usage_limit",
    "TemporarilyBlockedError": "blocked",
    "GeminiTimeoutError": "timeout",
    "TimeoutError": "timeout",
    "ModelInvalidError": "model_invalid",
}


class AccountStatusError(Exception):
    def __init__(self, error_type: str):
        super().__init__(error_type)
        self.error_type = error_type


def _map_account_status(status: Any) -> str | None:
    name = getattr(status, "name", None)
    if name is None or name == "AVAILABLE":
        return None
    if name == "UNAUTHENTICATED":
        return "auth"
    if name == "TOS_PENDING":
        return "tos"
    if name in ("ACCOUNT_REJECTED", "ACCOUNT_UNTRUSTED"):
        return "abuse"
    if name == "ACCESS_TEMPORARILY_UNAVAILABLE":
        return "blocked"
    return "blocked"


def _real_client_factory(cookies: dict, proxy: str):
    """Lazy-import GeminiClient. Only called at runtime, never in tests."""
    from gemini_webapi import GeminiClient  # type: ignore
    psid = cookies.get("__Secure-1PSID", "")
    psidts = cookies.get("__Secure-1PSIDTS", "")
    extra = {k: v for k, v in cookies.items()
             if k not in ("__Secure-1PSID", "__Secure-1PSIDTS")}
    return GeminiClient(
        secure_1psid=psid,
        secure_1psidts=psidts or "",
        cookies=extra or None,
        proxy=proxy,
    )


class GWSServer:
    """Main server orchestrator."""

    def __init__(
        self,
        *,
        bind: str = "172.17.0.1",
        port: int = 8098,
        token: str,
        accounts_dir: str = "/etc/gemini-web/accounts",
        state_path: str = "/var/lib/gemini-web-serve/state.json",
        default_proxy: str = "socks5h://100.64.96.50:1080",
        model: str = "gemini-argon",
        request_timeout: int = 600,
        watchdog_timeout: int = 300,
        thinking: str = "request",
        scheduler_cfg: SchedulerConfig | None = None,
        client_factory=None,
        pause_range: tuple[float, float] = (2.0, 6.0),
        rng: random.Random | None = None,
        quota_rest_below: float = 0.2,
    ):
        self.bind = bind
        self.port = port
        self.token = token
        self.accounts_dir = accounts_dir
        self.state_path = state_path
        self.default_proxy = default_proxy
        self.model = model
        self.request_timeout = request_timeout
        self.watchdog_timeout = watchdog_timeout
        # "request" honours the caller's extended_thinking flag; "standard"/"extended" force one level.
        self.thinking = thinking if thinking in ("request", "standard", "extended") else "request"
        self.cfg = scheduler_cfg or SchedulerConfig()
        self.client_factory = client_factory or _real_client_factory
        self.pause_range = pause_range
        self.rng = rng or random.Random()
        self.quota_rest_below = quota_rest_below

        self.states: dict[str, dict] = {}
        self.acct_data: dict[str, dict] = {}  # loaded account file data
        self.acct_paths: dict[str, Any] = {}  # name -> Path
        self.clients: dict[str, Any] = {}  # name -> client instance
        self._current_day: date | None = None
        self._active_names: list[str] = []

        # Asyncio event loop in background thread
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._gemini_lock: asyncio.Lock | None = None
        self._housekeeping_task: asyncio.Task | None = None
        self._httpd: ThreadingHTTPServer | None = None
        self._shutdown = False

    def start(self) -> None:
        """Load state, start async loop, start HTTP server."""
        self._load_accounts_and_state()
        self._ensure_day_plan(time.time())

        # Start asyncio loop in background thread
        self._loop = asyncio.new_event_loop()
        self._gemini_lock = asyncio.Lock()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        handler = partial(_RequestHandler, self)
        self._httpd = ThreadingHTTPServer((self.bind, self.port), handler)
        # Allow getting the actual port (useful when port=0)
        self.port = self._httpd.server_address[1]

        signal.signal(signal.SIGTERM, lambda *_: self.shutdown())
        log.info("Listening on %s:%d", self.bind, self.port)
        self._httpd.serve_forever()

    def start_background(self) -> None:
        """Start for tests: HTTP in a thread, returns immediately."""
        self._load_accounts_and_state()
        self._ensure_day_plan(time.time())

        self._loop = asyncio.new_event_loop()
        self._gemini_lock = asyncio.Lock()
        self._loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self._loop_thread.start()

        handler = partial(_RequestHandler, self)
        self._httpd = ThreadingHTTPServer((self.bind, self.port), handler)
        self.port = self._httpd.server_address[1]
        t = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        t.start()

    def shutdown(self) -> None:
        self._shutdown = True
        if self._housekeeping_task and not self._housekeeping_task.done():
            self._loop.call_soon_threadsafe(self._housekeeping_task.cancel)
        # Close clients and persist
        if self._loop and self._loop.is_running():
            fut = asyncio.run_coroutine_threadsafe(self._close_all_clients(), self._loop)
            try:
                fut.result(timeout=10)
            except Exception:
                pass
        self._save_state()
        if self._httpd:
            self._httpd.shutdown()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._housekeeping_task = self._loop.create_task(self._housekeeping_loop())
        self._loop.run_forever()  # type: ignore

    async def _housekeeping_loop(self) -> None:
        while not self._shutdown:
            try:
                await asyncio.sleep(300)
                await self._housekeeping(time.time())
            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.debug("housekeeping error: %s", exc)

    async def _housekeeping(self, now: float) -> None:
        """Close and persist cached clients that are sleeping, on break, or not ok."""
        for name in list(self.clients.keys()):
            st = self.states.get(name)
            should_close = False
            if not st:
                should_close = True
            else:
                avail, reason = is_available(st, now)
                if not avail and reason in (
                    "sleeping", "on_break", "needs_login", "abuse_flag", "cooldown", "no_proxy", "needs_tos", "quota_low"
                ):
                    should_close = True
                elif st.get("status") not in (None, "ok"):
                    should_close = True

            if should_close:
                client = self.clients.get(name)
                if client:
                    try:
                        self._persist_cookies(name, client)
                    except Exception:
                        pass
                await self._close_client(name)

    async def _close_all_clients(self) -> None:
        for name, client in list(self.clients.items()):
            try:
                self._persist_cookies(name, client)
                await client.close()
            except Exception:
                pass
        self.clients.clear()

    def _load_accounts_and_state(self) -> None:
        self.states = load_state(self.state_path)
        self._reload_account_files()

    def _reload_account_files(self) -> None:
        """Re-read account files, update statuses."""
        files = list_account_files(self.accounts_dir)
        self.acct_paths = files
        for name, path in files.items():
            self._update_account_from_file(name, path)

        for name in list(self.states.keys()):
            if name not in files:
                self.states.pop(name, None)
                self.acct_data.pop(name, None)

        self._update_active_names()

    def _update_account_from_file(self, name: str, path: Path) -> None:
        mtime = path.stat().st_mtime
        old_mtime = (self.states.get(name) or {}).get("file_mtime")
        if name not in self.states:
            self.states[name] = default_account_state()

        data = load_account_file(path)
        self.acct_data[name] = data
        status, _ = account_status(data, self.default_proxy)
        st = self.states[name]

        if old_mtime is not None and mtime != old_mtime:
            if st.get("status") in ("needs_login", "needs_tos") and status == "ok":
                st["status"] = "ok"
                st["cooldown_level"] = 0
                st["cooldown_until"] = None
            if name in self.clients:
                asyncio.run_coroutine_threadsafe(self._close_client(name), self._loop)
        elif old_mtime is None or st.get("status") is None:
            st["status"] = status

        if status == "no_proxy":
            st["status"] = "no_proxy"
        elif status == "needs_login":
            st["status"] = "needs_login"
        st["file_mtime"] = mtime

    async def _close_client(self, name: str) -> None:
        client = self.clients.pop(name, None)
        if client:
            try:
                await client.close()
            except Exception:
                pass

    def _update_active_names(self) -> None:
        names = sorted(
            n for n, st in self.states.items()
            if st.get("status") not in ("needs_login", "no_proxy", "needs_tos")
        )
        self._active_names = names

    def _ensure_day_plan(self, now: float) -> None:
        """Re-plan if day changed or active set changed."""
        tz_off = _tz(self.cfg.tz)
        today = _local_day(now, tz_off)
        self._update_active_names()

        old_names = sorted(
            n for n, st in self.states.items()
            if st.get("day") == today.isoformat() and
            st.get("status") not in ("needs_login", "no_proxy", "needs_tos")
        )
        need_replan = (self._current_day != today or old_names != self._active_names)

        if need_replan:
            plans = plan_day(self._active_names, today, self.cfg, self.rng)
            day_str = today.isoformat()
            for name, plan in plans.items():
                st = self.states.setdefault(name, default_account_state())
                if st.get("day") != day_str:
                    st["used_today"] = 0
                st["cap_today"] = plan.cap_today
                st["sleep_start"] = plan.sleep_start
                st["sleep_end"] = plan.sleep_end
                st["breaks"] = plan.breaks
                st["day"] = day_str
            self._current_day = today
            self._save_state()

    def _save_state(self) -> None:
        save_state(self.state_path, self.states)

    def _persist_cookies(self, name: str, client: Any) -> None:
        """Write client cookies back to account file if changed."""
        if name not in self.acct_data or name not in self.acct_paths:
            return
        data = self.acct_data[name]
        jar = getattr(getattr(client, "cookies", None), "jar", None)
        if jar is None:
            return
        if merge_cookies_from_jar(data, jar):
            save_account_file(self.acct_paths[name], data)
            if name in self.states:
                try:
                    self.states[name]["file_mtime"] = self.acct_paths[name].stat().st_mtime
                except OSError:
                    pass

    def _check_auth(self, auth_header: str | None) -> bool:
        if not auth_header:
            return False
        parts = auth_header.split(" ", 1)
        if len(parts) != 2 or parts[0] != "Bearer":
            return False
        return hmac.compare_digest(parts[1], self.token)

    def _check_proxy(self) -> bool:
        """TCP connect check to proxy host:port, 5s timeout."""
        proxy = self.default_proxy
        if not proxy:
            return False
        try:
            from urllib.parse import urlparse
            parsed = urlparse(proxy)
            host = parsed.hostname or ""
            port = parsed.port or 1080
        except Exception:
            return False
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.close()
            return True
        except (OSError, socket.timeout):
            return False

    def _validate_generate_request(
        self, body: Any
    ) -> tuple[int, dict] | tuple[None, tuple[str, str, bool]]:
        if not isinstance(body, dict):
            return 400, {"error": "bad_request"}
        prompt = body.get("prompt")
        if not isinstance(prompt, str) or len(prompt) < 1 or len(prompt) > 100000:
            return 400, {"error": "bad_request"}
        model = body.get("model", self.model)
        if not isinstance(model, str) or not _MODEL_RE.match(model):
            return 400, {"error": "bad_request"}
        extended_thinking = body.get("extended_thinking", True)
        if not isinstance(extended_thinking, bool):
            return 400, {"error": "bad_request"}
        if self.thinking != "request":
            extended_thinking = self.thinking == "extended"
        return None, (prompt, model, extended_thinking)

    def _no_account_ready_response(self, now: float) -> tuple[int, dict]:
        reasons = {n: is_available(st, now)[1] for n, st in self.states.items()}
        ra = next_ready_at(self.states, now, self.cfg.tz)
        retry_s = max(1, int((ra or now + 60) - now))
        return 503, {
            "error": "no_account_ready",
            "retry_after_s": retry_s,
            "reasons": reasons,
        }

    async def _update_quota_state(self, name: str, client: Any) -> None:
        """Fetch quotas from client and update state. Never raises."""
        try:
            fetch = getattr(client, "_fetch_quota", None)
            if fetch is not None:
                await fetch()
            quotas = getattr(client, "quotas", {}) or {}
            if not isinstance(quotas, dict):
                return
            # Find the bucket with the lowest remaining fraction
            low_fraction: float | None = None
            low_reset: float | None = None
            for bucket in quotas.values():
                if not isinstance(bucket, dict):
                    continue
                total = bucket.get("total")
                remaining = bucket.get("remaining")
                if not isinstance(total, (int, float)) or total <= 0:
                    continue
                if not isinstance(remaining, (int, float)):
                    continue
                frac = remaining / total
                if low_fraction is None or frac < low_fraction:
                    low_fraction = frac
                    low_reset = bucket.get("reset_time")

            st = self.states.get(name, {})
            st["quota_min_fraction"] = low_fraction
            st["quota_checked_at"] = time.time()
            if low_reset is not None and isinstance(low_reset, (int, float)) and low_reset > 0:
                st["quota_reset_at"] = float(low_reset)
            else:
                st["quota_reset_at"] = None
        except Exception:
            pass

    async def _apply_quota_rest_if_needed(self, name: str) -> bool:
        """If quota is below threshold, mark account quota_low and return True."""
        st = self.states.get(name, {})
        low_fraction = st.get("quota_min_fraction")
        if low_fraction is None:
            return False
        if low_fraction >= self.quota_rest_below:
            return False
        # Apply quota_low rest
        now = time.time()
        reset_at = st.get("quota_reset_at")
        jitter = self.rng.uniform(5, 15) * 60.0  # 5-15 min in seconds
        if reset_at is not None and isinstance(reset_at, (int, float)) and reset_at > now:
            cooldown_until = reset_at + jitter
        else:
            cooldown_until = now + 3600.0
        # Store jitter on state temporarily so apply_error can use it
        st["_quota_jitter"] = jitter
        st["quota_reset_at"] = reset_at
        from scheduler import apply_error
        apply_error(st, "quota_low", now)
        # Override cooldown_until with the correct one (apply_error uses _quota_jitter)
        st["cooldown_until"] = cooldown_until
        st.pop("_quota_jitter", None)
        log.info("quota_low account=%s fraction=%.3f rest_until=%.0f", name, low_fraction, cooldown_until)
        self._save_state()
        return True

    async def _execute_attempt(
        self, name: str, prompt: str, model: str, extended_thinking: bool
    ) -> tuple[int, dict]:
        """Run single generation attempt. Returns (status_code, result_dict)."""
        client = await self._get_or_create_client(name)
        # If quota check during init caused quota_low, treat as error
        st = self.states.get(name, {})
        if st.get("status") == "quota_low":
            raise AccountStatusError("quota_low")
        status_err = _map_account_status(getattr(client, "account_status", None))
        if status_err is not None:
            raise AccountStatusError(status_err)

        abuse = getattr(client, "abuse_status", None)
        if abuse is not None:
            is_clean = abuse.get("is_clean", True) if isinstance(abuse, dict) else getattr(abuse, "is_clean", True)
            if not is_clean:
                raise Exception("abuse_flag")

        p_min, p_max = self.pause_range
        if p_max > 0:
            await asyncio.sleep(self.rng.uniform(p_min, p_max))

        t0 = time.time()
        out = await asyncio.wait_for(
            client.generate_content(prompt, model=model, extended_thinking=extended_thinking),
            timeout=self.request_timeout,
        )
        text = getattr(out, "text", None) or ""
        if not text:
            raise TimeoutError("empty_text")

        now_after = time.time()
        after_use(self.states[name], now_after, self.cfg, self.rng)
        # Update quota after successful generate (never fails)
        await self._update_quota_state(name, client)
        self._persist_cookies(name, client)
        self._save_state()
        elapsed = now_after - t0
        log.info("generate account=%s model=%s prompt_len=%d elapsed=%.1f",
                 name, model, len(prompt), elapsed)
        thoughts = getattr(out, "thoughts", None) or ""
        return 200, {
            "text": text,
            "thoughts_chars": len(thoughts),
            "account": name,
            "seconds": round(elapsed, 2),
        }

    async def _handle_generate(self, body: Any) -> tuple[int, dict]:
        err, validated = self._validate_generate_request(body)
        if err is not None:
            return err, validated  # type: ignore

        prompt, model, extended_thinking = validated  # type: ignore
        now = time.time()
        if self._check_account_file_changes():
            self._update_active_names()
        self._ensure_day_plan(now)
        await self._housekeeping(now)

        if not self._check_proxy():
            return 503, {"error": "proxy_down"}

        accounts_tried: list[str] = []
        last_error_type = ""

        for attempt in range(2):
            now = time.time()
            if attempt == 0:
                name = pick_account(self.states, now, self.rng)
                if name is None:
                    return self._no_account_ready_response(now)
            else:
                avail = {n: st for n, st in self.states.items() if n not in accounts_tried}
                name = pick_account(avail, now, self.rng)
                if name is None:
                    return 502, {"error": last_error_type, "accounts_tried": accounts_tried}

            accounts_tried.append(name)
            try:
                return await self._execute_attempt(name, prompt, model, extended_thinking)
            except Exception as exc:
                if isinstance(exc, AccountStatusError):
                    error_type = exc.error_type
                elif str(exc) == "abuse_flag":
                    error_type = "abuse"
                else:
                    error_type = _map_error(exc)
                if error_type == "model_invalid":
                    return 400, {"error": "model_invalid"}
                # quota_low: already applied to state in _get_or_create_client; don't re-apply
                if error_type != "quota_low":
                    apply_error(self.states[name], error_type, time.time())
                if name in self.clients:
                    self._persist_cookies(name, self.clients[name])
                if error_type != "timeout":
                    await self._close_client(name)
                self._save_state()
                last_error_type = error_type

        return 502, {"error": last_error_type, "accounts_tried": accounts_tried}

    def _check_account_file_changes(self) -> bool:
        """Reload account files whose mtime changed. Returns True if changed."""
        changed = False
        for name, path in list(self.acct_paths.items()):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            st = self.states.get(name, {})
            if st.get("file_mtime") != mtime:
                data = load_account_file(path)
                self.acct_data[name] = data
                status, _ = account_status(data, self.default_proxy)
                if st.get("status") in ("needs_login", "needs_tos") and status == "ok":
                    st["status"] = "ok"
                    st["cooldown_level"] = 0
                    st["cooldown_until"] = None
                st["file_mtime"] = mtime
                changed = True
        return changed

    async def _get_or_create_client(self, name: str) -> Any:
        """Get cached client or create a new one."""
        if name in self.clients:
            return self.clients[name]

        data = self.acct_data.get(name, {})
        cookies = data.get("cookies", {})
        proxy = data.get("proxy", "") or self.default_proxy

        client = self.client_factory(cookies, proxy)
        refresh_interval = self.rng.randint(600, 1200)
        await client.init(
            timeout=self.request_timeout,
            auto_close=False,
            auto_refresh=True,
            refresh_interval=refresh_interval,
            watchdog_timeout=self.watchdog_timeout,
        )
        self.clients[name] = client
        # Check quota after init (never fails the init)
        await self._update_quota_state(name, client)
        # If quota already low, rest account — caller will fall over to another
        await self._apply_quota_rest_if_needed(name)
        return client

    def handle_health(self) -> tuple[int, dict]:
        now = time.time()
        self._ensure_day_plan(now)
        if self._loop and self._loop.is_running():
            if threading.current_thread() is self._loop_thread:
                asyncio.create_task(self._housekeeping(now))
            else:
                try:
                    fut = asyncio.run_coroutine_threadsafe(self._housekeeping(now), self._loop)
                    fut.result(timeout=5)
                except Exception:
                    pass
        proxy_ok = self._check_proxy()

        accts = []
        ready_now = 0
        any_later = False
        for name in sorted(self.states.keys()):
            st = self.states[name]
            avail, reason = is_available(st, now)
            if avail:
                ready_now += 1
            if st.get("status") not in ("needs_login", "no_proxy", "needs_tos") and reason != "daily_cap":
                any_later = True
            qf = st.get("quota_min_fraction")
            accts.append({
                "name": name,
                "status": st.get("status", "ok"),
                "available": avail,
                "reason": reason,
                "used_today": st.get("used_today", 0),
                "cap_today": st.get("cap_today", 0),
                "next_ok_at": st.get("next_ok_at"),
                "cooldown_until": st.get("cooldown_until"),
                "sleep_start": st.get("sleep_start"),
                "sleep_end": st.get("sleep_end"),
                "last_error_type": st.get("last_error_type"),
                "last_used_at": st.get("last_used_at"),
                "quota_pct_left": int(round(qf * 100)) if qf is not None else None,
                "quota_reset_at": st.get("quota_reset_at"),
            })

        ok = proxy_ok and (ready_now > 0 or any_later)
        return 200, {
            "ok": ok,
            "proxy_ok": proxy_ok,
            "ready_now": ready_now,
            "accounts": accts,
            "now": now,
        }

    def handle_reload(self) -> tuple[int, dict]:
        self._reload_account_files()
        for name, st in self.states.items():
            if st.get("status") in ("needs_login", "needs_tos"):
                data = self.acct_data.get(name, {})
                status, _ = account_status(data, self.default_proxy)
                if status == "ok":
                    st["status"] = "ok"
                    st["cooldown_level"] = 0
                    st["cooldown_until"] = None
        now = time.time()
        self._current_day = None  # force re-plan
        self._ensure_day_plan(now)
        return self.handle_health()


def _map_error(exc: Exception) -> str:
    """Map exception to error type string by class name."""
    if isinstance(exc, ValueError) and "Unknown model name" in str(exc):
        return "auth"
    cls_name = type(exc).__name__
    if cls_name in _ERROR_MAP:
        return _ERROR_MAP[cls_name]
    # asyncio.TimeoutError
    for base in type(exc).__mro__:
        if base.__name__ in _ERROR_MAP:
            return _ERROR_MAP[base.__name__]
    return "network"


class _RequestHandler(BaseHTTPRequestHandler):
    """HTTP request handler."""

    def __init__(self, server_obj: GWSServer, *args, **kwargs):
        self.gws = server_obj
        super().__init__(*args, **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        log.info(format, *args)

    def _check_auth(self) -> bool:
        auth = self.headers.get("Authorization")
        if not self.gws._check_auth(auth):
            self._json_response(401, {"error": "unauthorized"})
            return False
        return True

    def _read_body(self, max_size: int = _MAX_BODY) -> bytes | None:
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            length = 0
        if length > max_size:
            self._json_response(413, {"error": "body_too_large"})
            return None
        if length > 0:
            return self.rfile.read(length)
        return b""

    def _json_response(self, code: int, data: dict) -> None:
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/health":
            if not self._check_auth():
                return
            code, data = self.gws.handle_health()
            self._json_response(code, data)
        else:
            self._json_response(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if not self._check_auth():
            return

        raw = self._read_body()
        if raw is None:
            return

        if self.path == "/v1/generate":
            try:
                body = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                self._json_response(400, {"error": "bad_request"})
                return
            if not isinstance(body, dict):
                self._json_response(400, {"error": "bad_request"})
                return
            # Run in the async loop
            loop = self.gws._loop
            lock = self.gws._gemini_lock

            async def _run():
                async with lock:
                    return await self.gws._handle_generate(body)

            fut = asyncio.run_coroutine_threadsafe(_run(), loop)
            try:
                code, data = fut.result(timeout=self.gws.request_timeout + 30)
            except Exception as exc:
                log.error("generate failed: %s", exc)
                self._json_response(502, {"error": "network"})
                return
            self._json_response(code, data)

        elif self.path == "/v1/reload":
            code, data = self.gws.handle_reload()
            self._json_response(code, data)
        else:
            self._json_response(404, {"error": "not_found"})


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        stream=sys.stderr,
    )

    token = os.environ.get("GWS_TOKEN", "")
    if not token or len(token) < 24:
        log.error("GWS_TOKEN must be set and >= 24 chars")
        sys.exit(1)

    cfg = SchedulerConfig(
        sleep_min_h=float(os.environ.get("GWS_SLEEP_MIN_H", "3.5")),
        sleep_max_h=float(os.environ.get("GWS_SLEEP_MAX_H", "4.5")),
        gap_min_m=float(os.environ.get("GWS_GAP_MIN_M", "4")),
        gap_max_m=float(os.environ.get("GWS_GAP_MAX_M", "9")),
        cap_min=int(os.environ.get("GWS_CAP_MIN", "30")),
        cap_max=int(os.environ.get("GWS_CAP_MAX", "40")),
        breaks_max=int(os.environ.get("GWS_BREAKS_MAX", "2")),
    )

    server = GWSServer(
        bind=os.environ.get("GWS_BIND", "172.17.0.1"),
        port=int(os.environ.get("GWS_PORT", "8098")),
        token=token,
        accounts_dir=os.environ.get("GWS_ACCOUNTS_DIR", "/etc/gemini-web/accounts"),
        state_path=os.environ.get("GWS_STATE_PATH", "/var/lib/gemini-web-serve/state.json"),
        default_proxy=os.environ.get("GWS_DEFAULT_PROXY", "socks5h://100.64.96.50:1080"),
        model=os.environ.get("GWS_MODEL", "gemini-argon"),
        request_timeout=int(os.environ.get("GWS_REQUEST_TIMEOUT_S", "600")),
        # A short watchdog makes gemini_webapi resend the prompt in a new chat (each resend spends quota) when
        # the phone proxy cuts an idle stream after ~60 s; 300 s lets it poll until Argon has finished.
        watchdog_timeout=int(os.environ.get("GWS_WATCHDOG_TIMEOUT_S", "300")),
        thinking=os.environ.get("GWS_THINKING", "request"),
        scheduler_cfg=cfg,
        quota_rest_below=float(os.environ.get("GWS_QUOTA_REST_BELOW", "0.2")),
    )
    server.start()


if __name__ == "__main__":
    main()
