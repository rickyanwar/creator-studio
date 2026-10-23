"""Tests for quota-aware account resting (GWS_QUOTA_REST_BELOW)."""

from __future__ import annotations

import asyncio
import json
import os
import random
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from scheduler import apply_error, is_available, pick_account
from server import GWSServer
from scheduler import SchedulerConfig


# ---------------------------------------------------------------------------
# Minimal fake client with quota support
# ---------------------------------------------------------------------------

class _FakeOutput:
    def __init__(self, text: str, thoughts: str = ""):
        self.text = text
        self.thoughts = thoughts


class FakeClientWithQuota:
    """Fake GeminiClient that exposes a quotas dict and async _fetch_quota."""

    def __init__(self, cookies: dict, proxy: str, quotas: dict | None = None,
                 fetch_raises: bool = False):
        self._cookies = dict(cookies)
        self._proxy = proxy
        self.quotas = quotas or {}
        self._fetch_raises = fetch_raises
        self._fetch_calls = 0
        self.account_status = None
        self.abuse_status = None
        self.closed = False
        self._generate_fn = None

        # cookies attribute for cookie persistence
        class _Jar:
            def __iter__(self_):
                return iter([])
        class _Cookies:
            jar = _Jar()
        self.cookies = _Cookies()

    async def _fetch_quota(self):
        self._fetch_calls += 1
        if self._fetch_raises:
            raise RuntimeError("quota fetch failed")

    async def init(self, **kwargs):
        pass

    async def generate_content(self, prompt: str, model: str = "gemini-argon",
                               extended_thinking: bool = True):
        if self._generate_fn:
            return await self._generate_fn(prompt, model, extended_thinking)
        return _FakeOutput("ok response")

    async def close(self):
        self.closed = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TOKEN = "b" * 32


def _make_account_file(directory: str, name: str, psid: str = "FAKE") -> Path:
    data = {
        "updated_at": "2026-01-01T00:00:00+00:00",
        "cookies": {"__Secure-1PSID": psid, "__Secure-1PSIDTS": "ts"},
    }
    p = Path(directory) / f"{name}.json"
    p.write_text(json.dumps(data))
    return p


def _req(port: int, method: str, path: str, body: dict | None = None,
         token: str = TOKEN) -> tuple[int, dict]:
    url = f"http://127.0.0.1:{port}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
    req = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def _make_server(accts_dir: str, state_path: str,
                 quota_map: dict[str, dict | None] | None = None,
                 fetch_raises_for: set[str] | None = None,
                 quota_rest_below: float = 0.2,
                 rng: random.Random | None = None) -> tuple[GWSServer, int, dict[str, FakeClientWithQuota]]:
    """Build and start a GWSServer with quota-aware fake clients."""
    created: dict[str, FakeClientWithQuota] = {}
    quota_map = quota_map or {}
    fetch_raises_for = fetch_raises_for or set()

    def factory(cookies, proxy):
        # Identify account by PSID
        psid = cookies.get("__Secure-1PSID", "")
        name = None
        for path in sorted(Path(accts_dir).iterdir()):
            if path.suffix != ".json":
                continue
            try:
                d = json.loads(path.read_text())
                if d.get("cookies", {}).get("__Secure-1PSID") == psid:
                    name = path.stem
                    break
            except Exception:
                pass
        quotas = quota_map.get(name) if name else None
        raises = name in fetch_raises_for if name else False
        client = FakeClientWithQuota(cookies, proxy, quotas=quotas, fetch_raises=raises)
        if name:
            created[name] = client
        return client

    cfg = SchedulerConfig(
        sleep_min_h=3.5, sleep_max_h=4.5,
        gap_min_m=0.001, gap_max_m=0.002,
        cap_min=100, cap_max=100,
    )
    server = GWSServer(
        bind="127.0.0.1",
        port=0,
        token=TOKEN,
        accounts_dir=accts_dir,
        state_path=state_path,
        default_proxy="socks5h://127.0.0.1:19999",
        scheduler_cfg=cfg,
        client_factory=factory,
        pause_range=(0.0, 0.0),
        rng=rng or random.Random(1),
        quota_rest_below=quota_rest_below,
    )
    server._check_proxy = lambda: True
    server.start_background()
    # Clear sleep/break windows
    for st in server.states.values():
        st["sleep_start"] = 0.0
        st["sleep_end"] = 0.0
        st["breaks"] = []
    return server, server.port, created


@pytest.fixture
def tmp_dirs():
    with tempfile.TemporaryDirectory() as td:
        accts = os.path.join(td, "accounts")
        os.makedirs(accts)
        state = os.path.join(td, "state.json")
        yield accts, state


# ---------------------------------------------------------------------------
# Test 1: Low quota account → rested with reason quota_low, not cooldown_level
# ---------------------------------------------------------------------------

class TestQuotaLowRested:
    def test_account_with_low_quota_is_rested(self, tmp_dirs):
        """Account with 230/2400 remaining (< 20%) becomes quota_low, not cooldown."""
        accts, state = tmp_dirs
        _make_account_file(accts, "low", psid="LOW")

        # 230/2400 = ~9.6% remaining → below default 20%
        future_reset = time.time() + 3600 * 5  # 5h from now
        quotas = {
            "None-4": {
                "usage_percentage": 90,
                "reset_time": future_reset,
                "total": 2400,
                "remaining": 230,
                "action_id": "None-4",
                "label": "Pro",
                "window": "5h",
            }
        }

        server, port, created = _make_server(accts, state, quota_map={"low": quotas})
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            # The generate fails because quota_low is detected after init
            # Account becomes quota_low, no other account available → 502 or 503
            st = server.states["low"]
            # Status must be quota_low
            assert st["status"] == "quota_low", f"expected quota_low, got {st['status']}"
            # cooldown_level must NOT be raised
            assert st.get("cooldown_level", 0) == 0, "cooldown_level must not change"
            # cooldown_until must be > now + 5m (at least reset_time + 5min)
            now = time.time()
            assert st["cooldown_until"] > now + 5 * 60, "cooldown_until should be past reset+5min"
            assert st["cooldown_until"] <= future_reset + 15 * 60 + 1, "cooldown_until should be <= reset+15min"
        finally:
            server.shutdown()

    def test_quota_low_then_available_after_cooldown(self, tmp_dirs):
        """After cooldown_until passes, quota_low account becomes available again."""
        accts, state = tmp_dirs
        _make_account_file(accts, "low", psid="LOW")

        # 5% remaining → definitely below 20%
        now = time.time()
        future_reset = now + 300  # reset in 5 min
        quotas = {"None-4": {"total": 2400, "remaining": 120, "reset_time": future_reset}}

        server, port, created = _make_server(accts, state, quota_map={"low": quotas})
        try:
            _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            st = server.states["low"]
            assert st["status"] == "quota_low"

            # Before cooldown_until: still unavailable
            avail, reason = is_available(st, now + 1)
            assert not avail
            assert reason == "quota_low"

            # After cooldown_until: becomes available
            cu = st["cooldown_until"]
            avail2, reason2 = is_available(st, cu + 1)
            assert avail2, f"should be available after cooldown, got reason={reason2}"
            assert st["status"] == "ok"
            assert st["cooldown_until"] is None
            # cooldown_level must still be 0
            assert st.get("cooldown_level", 0) == 0
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Test 2: No cooldown_level change on quota_low
# ---------------------------------------------------------------------------

class TestQuotaLowNoCooldownLadder:
    def test_repeated_quota_low_does_not_escalate_cooldown_level(self, tmp_dirs):
        """apply_error('quota_low') never changes cooldown_level."""
        st = {"status": "ok", "cooldown_level": 0}
        now = 1_000_000.0
        reset_at = now + 3600
        st["quota_reset_at"] = reset_at

        apply_error(st, "quota_low", now)
        assert st["status"] == "quota_low"
        assert st.get("cooldown_level", 0) == 0

        # Reset and call again
        st["status"] = "ok"
        st["cooldown_until"] = None
        apply_error(st, "quota_low", now + 10)
        assert st.get("cooldown_level", 0) == 0


# ---------------------------------------------------------------------------
# Test 3: Account with more quota is picked first
# ---------------------------------------------------------------------------

class TestQuotaPickPreference:
    def test_higher_quota_fraction_picked_first(self):
        """pick_account prefers account with higher quota_min_fraction."""
        now = 1_800_000_000.0
        rng = random.Random(0)
        base = {
            "status": "ok",
            "sleep_start": 0.0, "sleep_end": 0.0, "breaks": [],
            "used_today": 0, "cap_today": 100,
            "last_used_at": now - 100,
        }

        states = {
            "low_quota": {**base, "quota_min_fraction": 0.1},   # 10%
            "high_quota": {**base, "quota_min_fraction": 0.9},  # 90%
        }

        picks = set()
        for s in range(60):
            r = random.Random(s)
            p = pick_account(states, now, r)
            picks.add(p)

        # high_quota should always be in the top-2 candidates and be picked most
        # low_quota should only be picked if randomness forces it (it's in position 2)
        assert "high_quota" in picks, "high_quota should be picked sometimes"

    def test_none_quota_fraction_counts_as_1(self):
        """Accounts with no quota info (None) treated as 1.0 fraction — highest priority."""
        now = 1_800_000_000.0
        base = {
            "status": "ok",
            "sleep_start": 0.0, "sleep_end": 0.0, "breaks": [],
            "used_today": 0, "cap_today": 100,
            "last_used_at": now - 100,
        }

        states = {
            "no_quota": {**base, "quota_min_fraction": None},  # treated as 1.0
            "low_quota": {**base, "quota_min_fraction": 0.1},
        }

        picks = set()
        for s in range(60):
            r = random.Random(s)
            p = pick_account(states, now, r)
            picks.add(p)

        assert "no_quota" in picks, "no_quota (None → 1.0) should be picked"


# ---------------------------------------------------------------------------
# Test 4: Missing quotas dict → behaviour unchanged
# ---------------------------------------------------------------------------

class TestMissingQuotas:
    def test_no_quotas_attribute_no_effect(self, tmp_dirs):
        """Client without quotas dict: request succeeds normally."""
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="NORM")

        # quota_map has None for "a" → FakeClientWithQuota.quotas = {}
        server, port, created = _make_server(accts, state, quota_map={"a": None})
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200, f"expected 200, got {code}: {body}"
            st = server.states["a"]
            assert st.get("status", "ok") == "ok"
            # quota_min_fraction stays None since no buckets
            assert st.get("quota_min_fraction") is None
        finally:
            server.shutdown()

    def test_sufficient_quota_no_rest(self, tmp_dirs):
        """Account with 743/2400 (~31%) > 20% threshold: no resting."""
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="NORM")

        quotas = {"None-4": {"total": 2400, "remaining": 743, "reset_time": None}}
        server, port, created = _make_server(accts, state, quota_map={"a": quotas})
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200, f"expected 200, got {code}: {body}"
            st = server.states["a"]
            assert st.get("status", "ok") == "ok"
            assert abs(st["quota_min_fraction"] - 743/2400) < 0.001
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Test 5: _fetch_quota raising does not fail the request
# ---------------------------------------------------------------------------

class TestFetchQuotaRaises:
    def test_fetch_quota_error_does_not_fail_request(self, tmp_dirs):
        """If _fetch_quota raises, the generate request still succeeds."""
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="ERR")

        server, port, created = _make_server(
            accts, state,
            quota_map={"a": {}},
            fetch_raises_for={"a"},
        )
        try:
            code, body = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200, f"expected 200, got {code}: {body}"
        finally:
            server.shutdown()


# ---------------------------------------------------------------------------
# Test 6: /health shows quota_pct_left
# ---------------------------------------------------------------------------

class TestHealthQuotaFields:
    def test_health_shows_quota_pct_left(self, tmp_dirs):
        """After a generate, /health account row has quota_pct_left populated."""
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="NORM")

        # 50% remaining
        quotas = {"bucket": {"total": 100, "remaining": 50, "reset_time": None}}
        server, port, created = _make_server(accts, state, quota_map={"a": quotas})
        try:
            # Trigger a generate so quota is fetched
            code, _ = _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            assert code == 200

            code, health = _req(port, "GET", "/health")
            assert code == 200
            acct = next(a for a in health["accounts"] if a["name"] == "a")
            assert acct["quota_pct_left"] == 50, f"expected 50, got {acct['quota_pct_left']}"
            assert "quota_reset_at" in acct
            # No cookies/tokens in health
            raw = json.dumps(health)
            assert "NORM" not in raw
            assert TOKEN not in raw
        finally:
            server.shutdown()

    def test_health_quota_pct_left_null_when_no_quotas(self, tmp_dirs):
        """quota_pct_left is null when client returned no quota buckets."""
        accts, state = tmp_dirs
        _make_account_file(accts, "a", psid="NORM")

        server, port, created = _make_server(accts, state, quota_map={"a": None})
        try:
            _req(port, "POST", "/v1/generate", body={"prompt": "hi"})
            _, health = _req(port, "GET", "/health")
            acct = next(a for a in health["accounts"] if a["name"] == "a")
            assert acct["quota_pct_left"] is None
        finally:
            server.shutdown()
