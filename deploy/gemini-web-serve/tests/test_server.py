"""Tests for server.py — HTTP endpoints with fake GeminiClient."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from server import GWSServer
from scheduler import SchedulerConfig


# ---------------------------------------------------------------------------
# Fake exception classes (matched by __name__)
# ---------------------------------------------------------------------------

class AuthError(Exception):
    pass

class UsageLimitExceededError(Exception):
    pass

class TemporarilyBlockedError(Exception):
    pass

class ModelInvalidError(Exception):
    pass


# ---------------------------------------------------------------------------
# Fake GeminiClient
# ---------------------------------------------------------------------------

class _FakeCookie:
    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


class _FakeJar:
    def __init__(self, cookies: dict[str, str]):
        self._cookies = cookies

    def __iter__(self):
        return iter([_FakeCookie(k, v) for k, v in self._cookies.items()])


class _FakeCookies:
    def __init__(self, cookies: dict[str, str]):
        self.jar = _FakeJar(cookies)


class FakeAccountStatus:
    def __init__(self, name: str, value: int = 1000):
        self.name = name
        self.value = value


class FakeClient:
    """Fake GeminiClient for testing."""

    def __init__(self, cookies: dict[str, str], proxy: str):
        self._init_cookies = dict(cookies)
        self._proxy = proxy
        self.abuse_status = None
        self.account_status = None
        # After init, add a "refreshed" cookie to detect persistence
        self.cookies = _FakeCookies({**cookies, "__refreshed": "yes"})
        self._generate_fn = None  # set by test to control behavior
        self.closed = False
        self.init_kwargs: dict[str, Any] = {}

    async def init(self, timeout=600, auto_close=False, auto_refresh=True, refresh_interval=900, **kwargs):
        self.init_kwargs = {
            "timeout": timeout,
            "auto_close": auto_close,
            "auto_refresh": auto_refresh,
            "refresh_interval": refresh_interval,
            **kwargs,
        }

    async def generate_content(self, prompt: str, model: str = "gemini-argon",
                                extended_thinking: bool = True):
        if self._generate_fn:
            return await self._generate_fn(prompt, model, extended_thinking)
        return _FakeOutput("Hello from Gemini!", "Some thoughts here.")

    async def close(self):
        self.closed = True


class _FakeOutput:
    def __init__(self, text: str, thoughts: str | None = None):
        self.text = text
        self.thoughts = thoughts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TOKEN = "a" * 32  # 32 chars, meets >= 24


def _make_account_file(directory: str, name: str, psid: str = "FAKE_PSID",
                       proxy: str | None = None) -> Path:
    data = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "cookies": {"__Secure-1PSID": psid, "__Secure-1PSIDTS": "ts_val"},
    }
    if proxy is not None:
        data["proxy"] = proxy
    p = Path(directory) / f"{name}.json"
    p.write_text(json.dumps(data))
    return p


class ServerFixture:
    """Manages a test server instance."""

    def __init__(self, *, accounts_dir: str, state_path: str,
                 client_behaviors: dict[str, Any] | None = None,
                 client_statuses: dict[str, Any] | None = None,
                 default_proxy: str = "socks5h://127.0.0.1:19999",
                 proxy_check_ok: bool = True,
                 clear_sleeps: bool = True,
                 pause_range: tuple[float, float] = (0.0, 0.0),
                 rng: random.Random | None = None,
                 request_timeout: int = 600,
                 watchdog_timeout: int = 300,
                 thinking: str = "request"):
        self.accounts_dir = accounts_dir
        self.state_path = state_path
        self._client_behaviors = client_behaviors or {}
        self._client_statuses = client_statuses or {}
        self._proxy_check_ok = proxy_check_ok
        self._clear_sleeps = clear_sleeps
        self._clients_created: dict[str, FakeClient] = {}

        cfg = SchedulerConfig(
            sleep_min_h=3.5, sleep_max_h=4.5,
            gap_min_m=0.001, gap_max_m=0.002,  # tiny gap for tests
            cap_min=100, cap_max=100,  # high cap
        )

        def factory(cookies, proxy):
            c = FakeClient(cookies, proxy)
            # Find which account this is by matching PSID
            for path in sorted(Path(accounts_dir).iterdir()):
                if path.suffix != ".json":
                    continue
                try:
                    d = json.loads(path.read_text())
                    if d.get("cookies", {}).get("__Secure-1PSID") == cookies.get("__Secure-1PSID"):
                        self._clients_created[path.stem] = c
                        if path.stem in self._client_behaviors:
                            c._generate_fn = self._client_behaviors[path.stem]
                        if path.stem in self._client_statuses:
                            c.account_status = self._client_statuses[path.stem]
                        break
                except Exception:
                    pass
            return c

        self.server = GWSServer(
            bind="127.0.0.1",
            port=0,
            token=TOKEN,
            accounts_dir=accounts_dir,
            state_path=state_path,
            default_proxy=default_proxy,
            scheduler_cfg=cfg,
            client_factory=factory,
            pause_range=pause_range,
            rng=rng or random.Random(1),
            request_timeout=request_timeout,
            watchdog_timeout=watchdog_timeout,
            thinking=thinking,
        )
        # Monkeypatch proxy check
        self.server._check_proxy = lambda: self._proxy_check_ok

    def start(self):
        self.server.start_background()
        if self._clear_sleeps:
            # Clear sleep/break windows so accounts are available regardless of real time
            for st in self.server.states.values():
                st["sleep_start"] = 0.0
                st["sleep_end"] = 0.0
                st["breaks"] = []
        return self.server.port

    def stop(self):
        self.server.shutdown()


@pytest.fixture
def tmp_dirs():
    with tempfile.TemporaryDirectory() as td:
        accts = os.path.join(td, "accounts")
        os.makedirs(accts)
        state = os.path.join(td, "state.json")
        yield accts, state


def _req(port: int, method: str, path: str, body: dict | None = None,
         token: str | None = TOKEN, raw_body: bytes | None = None) -> tuple[int, dict]:
    """Make an HTTP request. Returns (status_code, json_body)."""
    url = f"http://127.0.0.1:{port}{path}"
    if raw_body is not None:
        data = raw_body
    elif body is not None:
        data = json.dumps(body).encode()
    else:
        data = None

    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"

    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_token_401(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            code, body = _req(port, "GET", "/health", token=None)
            assert code == 401
            assert body["error"] == "unauthorized"
        finally:
            sf.stop()

    def test_wrong_token_401(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            code, body = _req(port, "GET", "/health", token="wrong_token_value_here_x")
            assert code == 401
        finally:
            sf.stop()


class TestBodyLimits:
    def test_413_big_body(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            big = b"x" * (256 * 1024 + 1)
            code, body = _req(port, "POST", "/v1/generate", raw_body=big)
            assert code == 413
        finally:
            sf.stop()


class TestBadRequest:
    def test_empty_prompt_400(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": ""})
            assert code == 400
            assert body["error"] == "bad_request"
        finally:
            sf.stop()

    def test_bad_model_400(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate",
                              body={"prompt": "hi", "model": "BAD MODEL!"})
            assert code == 400
        finally:
            sf.stop()


class TestSuccessPath:
    def test_generate_success(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate",
                              body={"prompt": "Hello world"})
            assert code == 200
            assert body["text"] == "Hello from Gemini!"
            assert body["account"] == "a"
            assert "thoughts_chars" in body
            assert "seconds" in body

            # Cookie file should be updated with __refreshed cookie
            acct_file = Path(accts) / "a.json"
            data = json.loads(acct_file.read_text())
            assert data["cookies"].get("__refreshed") == "yes"

            # File mode should be 0o600
            mode = stat.S_IMODE(acct_file.stat().st_mode)
            assert mode == 0o600

            # State file should exist
            assert Path(state).exists()
        finally:
            sf.stop()


class TestAuthErrorFallover:
    def test_auth_error_tries_second_account(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        _make_account_file(accts, "b", psid="PSID_B")

        async def _fail_auth(prompt, model, et):
            raise AuthError("expired")

        behaviors = {"a": _fail_auth}
        sf = ServerFixture(accounts_dir=accts, state_path=state,
                           client_behaviors=behaviors)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate",
                              body={"prompt": "test"})
            assert code == 200
            assert body["account"] == "b"

            # Check health: a should be needs_login
            code, health = _req(port, "GET", "/health")
            assert code == 200
            acct_a = [a for a in health["accounts"] if a["name"] == "a"][0]
            assert acct_a["status"] == "needs_login"
        finally:
            sf.stop()


class TestUsageLimitBoth502:
    def test_both_usage_limit_502(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        _make_account_file(accts, "b", psid="PSID_B")

        async def _fail_limit(prompt, model, et):
            raise UsageLimitExceededError("limit")

        behaviors = {"a": _fail_limit, "b": _fail_limit}
        sf = ServerFixture(accounts_dir=accts, state_path=state,
                           client_behaviors=behaviors)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate",
                              body={"prompt": "test"})
            assert code == 502
            assert len(body.get("accounts_tried", [])) == 2

            # Both in cooldown
            _, health = _req(port, "GET", "/health")
            for acct in health["accounts"]:
                assert acct["status"] == "cooldown"
        finally:
            sf.stop()


class TestAllAsleep503:
    def test_all_asleep_503(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state, clear_sleeps=False)
        port = sf.start()
        try:
            # Force sleep window over current time
            now = time.time()
            sf.server.states["a"]["sleep_start"] = now - 100
            sf.server.states["a"]["sleep_end"] = now + 7200

            code, body = _req(port, "POST", "/v1/generate",
                              body={"prompt": "hello"})
            assert code == 503
            assert body["error"] == "no_account_ready"
            assert body["retry_after_s"] > 0
        finally:
            sf.stop()


class TestProxyDown503:
    def test_proxy_down_503(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state, proxy_check_ok=False)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate",
                              body={"prompt": "hello"})
            assert code == 503
            assert body["error"] == "proxy_down"
        finally:
            sf.stop()


class TestNoProxy:
    def test_no_proxy_account_never_used(self, tmp_dirs):
        accts, state = tmp_dirs
        # Account with no proxy and empty default
        _make_account_file(accts, "noproxy", proxy="")
        sf = ServerFixture(accounts_dir=accts, state_path=state,
                           default_proxy="")
        # Also need proxy check to fail since no proxy
        sf._proxy_check_ok = False
        port = sf.start()
        try:
            _, health = _req(port, "GET", "/health")
            acct = [a for a in health["accounts"] if a["name"] == "noproxy"][0]
            assert acct["status"] == "no_proxy"
        finally:
            sf.stop()


class TestHealthNoCookies:
    def test_health_no_cookies_no_token(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            code, body = _req(port, "GET", "/health")
            assert code == 200
            raw = json.dumps(body)
            assert "FAKE_PSID" not in raw
            assert TOKEN not in raw
            assert "__Secure-1PSID" not in raw
        finally:
            sf.stop()


class TestReload:
    def test_reload_picks_up_new_account(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            _, h1 = _req(port, "GET", "/health")
            names1 = [a["name"] for a in h1["accounts"]]
            assert "b" not in names1

            # Add new account file
            _make_account_file(accts, "b", psid="PSID_B")

            code, h2 = _req(port, "POST", "/v1/reload")
            assert code == 200
            names2 = [a["name"] for a in h2["accounts"]]
            assert "b" in names2
        finally:
            sf.stop()


class TestLogNoCookies:
    def test_log_no_cookie_values(self, tmp_dirs, caplog):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            with caplog.at_level(logging.DEBUG):
                _req(port, "POST", "/v1/generate", body={"prompt": "test"})
            log_text = caplog.text
            assert "FAKE_PSID" not in log_text
            assert "ts_val" not in log_text
        finally:
            sf.stop()


class TestModelInvalidError:
    def test_model_invalid_returns_400(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a")

        async def _fail_model(prompt, model, et):
            raise ModelInvalidError("bad model")

        sf = ServerFixture(accounts_dir=accts, state_path=state,
                           client_behaviors={"a": _fail_model})
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 400
            assert body["error"] == "model_invalid"
        finally:
            sf.stop()


class TestEmptyTextTimeoutFallover:
    def test_empty_text_falls_over(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        _make_account_file(accts, "b", psid="PSID_B")

        async def _empty_text(prompt, model, et):
            return _FakeOutput("")

        sf = ServerFixture(accounts_dir=accts, state_path=state,
                           client_behaviors={"a": _empty_text})
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200
            assert body["account"] == "b"
        finally:
            sf.stop()


class TestAbuseStatusFallover:
    def test_abuse_status_dirty_falls_over(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        _make_account_file(accts, "b", psid="PSID_B")

        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            # Trigger client creation for a and set abuse_status
            client_a = FakeClient({"__Secure-1PSID": "PSID_A"}, "proxy")
            client_a.abuse_status = {"is_clean": False, "status_code": 403, "signal": "bot"}
            sf.server.clients["a"] = client_a

            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200
            assert body["account"] == "b"
            assert sf.server.states["a"]["status"] == "abuse_flag"
        finally:
            sf.stop()


class TestTemporarilyBlocked:
    def test_temporarily_blocked_cooldown_fallover(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        _make_account_file(accts, "b", psid="PSID_B")

        async def _fail_blocked(prompt, model, et):
            raise TemporarilyBlockedError("rate limited")

        sf = ServerFixture(accounts_dir=accts, state_path=state,
                           client_behaviors={"a": _fail_blocked})
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200
            assert body["account"] == "b"
            assert sf.server.states["a"]["status"] == "cooldown"
        finally:
            sf.stop()


class TestHousekeeping:
    def test_client_closed_on_sleep_and_recreated(self, tmp_dirs, monkeypatch):
        accts, state = tmp_dirs
        p = _make_account_file(accts, "a", psid="PSID_A")

        current_time = 1770000000.0
        monkeypatch.setattr(time, "time", lambda: current_time)

        sf = ServerFixture(accounts_dir=accts, state_path=state, clear_sleeps=False)
        port = sf.start()
        try:
            # Set sleep window for account a later today
            sf.server.states["a"]["sleep_start"] = current_time + 100.0
            sf.server.states["a"]["sleep_end"] = current_time + 500.0
            sf.server.states["a"]["breaks"] = []

            # First generation creates and caches client
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200
            assert "a" in sf.server.clients
            client1 = sf.server.clients["a"]
            assert client1.closed is False

            # Advance time into the sleep window
            current_time = current_time + 200.0

            # Health check triggers housekeeping
            code, h = _req(port, "GET", "/health")
            assert code == 200
            assert "a" not in sf.server.clients
            assert client1.closed is True

            # Cookies were persisted before closing
            saved_data = json.loads(p.read_text())
            assert saved_data.get("cookies", {}).get("__refreshed") == "yes"

            # Advance time past the sleep window
            current_time = current_time + 600.0

            # Next request recreates client lazily
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi again"})
            assert code == 200
            assert "a" in sf.server.clients
            client2 = sf.server.clients["a"]
            assert client2 is not client1
            assert client2.closed is False
        finally:
            sf.stop()


class TestAccountStatusAndErrorMapping:
    def test_tos_pending_falls_over_to_next_account_and_retries_after_6h(self, tmp_dirs, monkeypatch):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        _make_account_file(accts, "b", psid="PSID_B")

        now_val = 1770000000.0
        monkeypatch.setattr(time, "time", lambda: now_val)

        statuses = {
            "a": FakeAccountStatus("TOS_PENDING", 1040),
            "b": FakeAccountStatus("AVAILABLE", 1000),
        }
        sf = ServerFixture(accounts_dir=accts, state_path=state, client_statuses=statuses)
        port = sf.start()
        try:
            # Force account 'a' to be preferred first
            sf.server.states["a"]["last_used_at"] = 1.0
            sf.server.states["b"]["last_used_at"] = 100.0

            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hello"})
            assert code == 200
            assert body["account"] == "b"

            # Account 'a' is marked needs_tos with cooldown 6h
            st_a = sf.server.states["a"]
            assert st_a["status"] == "needs_tos"
            assert st_a["last_error_type"] == "tos"
            assert st_a["cooldown_until"] == now_val + 6 * 3600

            # Health shows needs_tos
            code, h = _req(port, "GET", "/health")
            assert code == 200
            acct_h = {a["name"]: a for a in h["accounts"]}
            assert acct_h["a"]["status"] == "needs_tos"
            assert acct_h["a"]["reason"] == "needs_tos"
            assert acct_h["a"]["available"] is False

            # Before 6 h, still unavailable
            from scheduler import is_available
            avail, reason = is_available(st_a, now_val + 5 * 3600)
            assert avail is False
            assert reason == "needs_tos"

            # After 6 h, becomes available and status resets to ok
            avail, reason = is_available(st_a, now_val + 6 * 3600 + 1)
            assert avail is True
            assert st_a["status"] == "ok"
            assert st_a["cooldown_until"] is None
        finally:
            sf.stop()

    def test_unauthenticated_status_marks_needs_login(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        statuses = {"a": FakeAccountStatus("UNAUTHENTICATED", 1016)}
        sf = ServerFixture(accounts_dir=accts, state_path=state, client_statuses=statuses)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hello"})
            assert code == 502
            assert sf.server.states["a"]["status"] == "needs_login"
            assert sf.server.states["a"]["last_error_type"] == "auth"
        finally:
            sf.stop()

    def test_account_untrusted_status_marks_abuse_flag(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        statuses = {"a": FakeAccountStatus("ACCOUNT_UNTRUSTED", 1033)}
        sf = ServerFixture(accounts_dir=accts, state_path=state, client_statuses=statuses)
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hello"})
            assert code == 502
            assert sf.server.states["a"]["status"] == "abuse_flag"
            assert sf.server.states["a"]["last_error_type"] == "abuse"
        finally:
            sf.stop()

    def test_unknown_model_name_value_error_marks_needs_login(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")

        async def _fail(*_args, **_kwargs):
            raise ValueError("Unknown model name: 'gemini-argon'. Available registered models: gemini-flash-lite")

        sf = ServerFixture(accounts_dir=accts, state_path=state, client_behaviors={"a": _fail})
        port = sf.start()
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hello"})
            assert code == 502
            assert sf.server.states["a"]["status"] == "needs_login"
            assert sf.server.states["a"]["last_error_type"] == "auth"
        finally:
            sf.stop()

    def test_timeouts_defaults_and_watchdog_passed_to_init(self, tmp_dirs):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        sf = ServerFixture(accounts_dir=accts, state_path=state)
        port = sf.start()
        try:
            assert sf.server.request_timeout == 600
            assert sf.server.watchdog_timeout == 300
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hello"})
            assert code == 200
            client = sf._clients_created["a"]
            assert client.init_kwargs["watchdog_timeout"] == 300
            assert client.init_kwargs["timeout"] == 600
        finally:
            sf.stop()




class TestThinkingOverride:
    """GWS_THINKING forces one thinking level whatever the caller asks for."""

    def _run(self, tmp_dirs, thinking, requested):
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="PSID_A")
        seen = {}

        async def gen(prompt, model, extended_thinking):
            seen["extended_thinking"] = extended_thinking
            return _FakeOutput("ok", "")

        sf = ServerFixture(accounts_dir=accts, state_path=state, client_behaviors={"a": gen}, thinking=thinking)
        port = sf.start()
        try:
            code, _ = _req(port, "POST", "/v1/generate", body={"prompt": "hello", "extended_thinking": requested})
            assert code == 200
        finally:
            sf.stop()
        return seen["extended_thinking"]

    def test_standard_forces_off(self, tmp_dirs):
        assert self._run(tmp_dirs, "standard", True) is False

    def test_extended_forces_on(self, tmp_dirs):
        assert self._run(tmp_dirs, "extended", False) is True

    def test_request_honours_caller(self, tmp_dirs):
        assert self._run(tmp_dirs, "request", False) is False
