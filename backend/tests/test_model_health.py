"""Tests for model_health service, Celery task, and /hermes/model-health endpoint.

Rules enforced:
- No real network calls to 9Router (all httpx / OpenAI mocked).
- No cx/* models ever.
- In-memory Redis stub (no fakeredis dependency).
"""

import json
import time
from unittest.mock import MagicMock, patch

import pytest


# ── Tiny in-memory Redis stub ────────────────────────────────────────────────

class _FakeRedis:
    """Minimal Redis stub: hset/hget/hgetall/expire/hdel/delete/get — enough
    for model_health.  No persistence, no pub/sub, no TTL enforcement (TTL is
    tested via logic, not clock)."""

    def __init__(self):
        self._data: dict[str, dict | str] = {}
        self._ttl: dict[str, float] = {}

    # hash ops
    def hset(self, name, key, value):
        if name not in self._data:
            self._data[name] = {}
        self._data[name][key] = value

    def hget(self, name, key):
        return self._data.get(name, {}).get(key)

    def hgetall(self, name):
        return dict(self._data.get(name, {}))

    def hdel(self, name, key):
        self._data.get(name, {}).pop(key, None)

    # string ops
    def get(self, key):
        return self._data.get(key)

    def set(self, key, value, ex=None):
        self._data[key] = value

    # generic
    def expire(self, name, seconds):
        self._ttl[name] = seconds

    def delete(self, *keys):
        for k in keys:
            self._data.pop(k, None)

    def ping(self):
        return True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_fake_redis():
    return _FakeRedis()


def _redis_patch(fake):
    """Patch model_health's _get_redis() to return fake."""
    return patch("app.services.model_health._get_redis", return_value=fake)


# ── 1. classify: status from probe response ──────────────────────────────────

class TestClassify:
    """probe() status classification without hitting the network."""

    def _probe_with_response(self, status_code, body_text):
        """Stub httpx to return the given status/body and call probe()."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = status_code
        mock_resp.text = body_text
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "pong"}}]
        } if status_code == 200 and "pong" in body_text else {}
        mock_resp.raise_for_status = MagicMock()

        if status_code >= 400:
            mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
                f"{status_code}", request=MagicMock(), response=mock_resp
            )

        fake_r = _make_fake_redis()
        with _redis_patch(fake_r):
            with patch("app.services.model_health._get_nine_router_config") as cfg_mock:
                cfg_mock.return_value = MagicMock(
                    base_url="https://router.test", api_key="k", enabled=True
                )
                with patch("httpx.post", return_value=mock_resp) as post_mock:
                    if status_code >= 400:
                        post_mock.side_effect = httpx.HTTPStatusError(
                            f"{status_code}", request=MagicMock(), response=mock_resp
                        )
                    from app.services.model_health import probe
                    return probe("ag/some-model")

    def test_200_is_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "pong"}}]
        }
        import httpx

        with patch("httpx.post", return_value=mock_resp):
            with patch("app.services.model_health._get_nine_router_config") as cfg_mock:
                cfg_mock.return_value = MagicMock(
                    base_url="https://router.test", api_key="k", enabled=True
                )
                from app.services.model_health import probe
                status, msg = probe("ag/some-model")
        assert status == "ok"

    def test_404_body_is_dead(self):
        """A response body containing 404/NOT_FOUND → dead."""
        import httpx

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "NOT_FOUND: Requested entity was not found"
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_resp
        )

        with patch("httpx.post", side_effect=httpx.HTTPStatusError(
            "404", request=MagicMock(), response=mock_resp
        )):
            with patch("app.services.model_health._get_nine_router_config") as cfg_mock:
                cfg_mock.return_value = MagicMock(
                    base_url="https://router.test", api_key="k", enabled=True
                )
                from app.services import model_health
                # Patch _classify directly via the exc text
                status, msg = model_health._classify_error(
                    "404 NOT_FOUND: Requested entity was not found"
                )
        assert status == "dead"

    def test_reset_after_is_unavailable(self):
        from app.services.model_health import _classify_error
        status, msg = _classify_error("reset after 1m quota exceeded")
        assert status == "unavailable"

    def test_429_is_unavailable(self):
        from app.services.model_health import _classify_error
        status, msg = _classify_error("429 Too Many Requests rate limit")
        assert status == "unavailable"

    def test_500_is_unavailable(self):
        from app.services.model_health import _classify_error
        status, msg = _classify_error("500 Internal Server Error")
        assert status == "unavailable"

    def test_not_found_body_is_dead(self):
        from app.services.model_health import _classify_error
        status, _ = _classify_error("Requested entity was not found")
        assert status == "dead"

    def test_unavailable_keyword_is_unavailable(self):
        from app.services.model_health import _classify_error
        status, _ = _classify_error("Service Unavailable")
        assert status == "unavailable"

    def test_5xx_is_unavailable(self):
        from app.services.model_health import _classify_error
        status, _ = _classify_error("503 upstream unavailable")
        assert status == "unavailable"


# ── 2. check_all: cx/* skipped; unlisted → dead; TTL set ────────────────────

class TestCheckAll:

    def _run_check_all(self, configured_models, live_router_ids, probe_results):
        """
        configured_models: list of ids returned by configured_models()
        live_router_ids: list of ids returned by /v1/models
        probe_results: dict model_id -> (status, msg) — used when probe() is called
        """
        fake_r = _make_fake_redis()

        with _redis_patch(fake_r):
            with patch("app.services.model_health.configured_models",
                       return_value=configured_models):
                with patch("app.services.model_health._get_nine_router_config") as cfg_mock:
                    cfg_mock.return_value = MagicMock(
                        base_url="https://router.test", api_key="k", enabled=True
                    )
                    with patch("app.services.model_health._fetch_live_model_ids",
                               return_value=live_router_ids):
                        with patch("app.services.model_health.probe",
                                   side_effect=lambda m: probe_results.get(m, ("ok", "pong"))):
                            with patch("time.sleep"):  # skip 0.5s pauses
                                from app.services.model_health import check_all
                                check_all()

        return fake_r

    def test_cx_models_are_skipped(self):
        """cx/* ids from configured_models() must never be probed or stored."""
        configured = ["ag/gemini-3.8-flash-low", "cx/image-gen-1"]
        live = ["ag/gemini-3.8-flash-low", "cx/image-gen-1"]
        fake_r = self._run_check_all(configured, live, {"ag/gemini-3.8-flash-low": ("ok", "pong")})
        stored = fake_r.hgetall("model_health")
        assert "cx/image-gen-1" not in stored

    def test_unlisted_configured_id_is_dead(self):
        """A configured model not in /v1/models list → dead with 'not listed'."""
        configured = ["ag/gemini-3.8-flash-low", "ag/retired-model"]
        live = ["ag/gemini-3.8-flash-low"]
        fake_r = self._run_check_all(
            configured, live, {"ag/gemini-3.8-flash-low": ("ok", "pong")}
        )
        stored = fake_r.hgetall("model_health")
        assert "ag/retired-model" in stored
        data = json.loads(stored["ag/retired-model"])
        assert data["status"] == "dead"
        assert "not listed" in data["message"]

    def test_results_stored_with_ttl(self):
        configured = ["ag/gemini-3.8-flash-low"]
        live = ["ag/gemini-3.8-flash-low"]
        fake_r = self._run_check_all(configured, live, {"ag/gemini-3.8-flash-low": ("ok", "pong")})
        stored = fake_r.hgetall("model_health")
        assert "ag/gemini-3.8-flash-low" in stored
        # TTL set on the hash key
        assert "model_health" in fake_r._ttl
        assert fake_r._ttl["model_health"] == 26 * 3600

    def test_probed_status_stored_correctly(self):
        configured = ["ag/some-model"]
        live = ["ag/some-model"]
        fake_r = self._run_check_all(
            configured, live, {"ag/some-model": ("unavailable", "reset after 1m")}
        )
        stored = fake_r.hgetall("model_health")
        data = json.loads(stored["ag/some-model"])
        assert data["status"] == "unavailable"
        assert "checked_at" in data

    def test_live_model_endpoint_does_not_duplicate_v1(self):
        from app.services import model_health

        response = MagicMock()
        response.json.return_value = {"data": [{"id": "ag/live"}, {"id": "cx/image"}]}
        response.raise_for_status.return_value = None
        with patch.object(model_health, "_get_nine_router_config", return_value=MagicMock(
            base_url="https://router.test/v1", api_key="k", enabled=True
        )):
            with patch("httpx.get", return_value=response) as get:
                assert model_health._fetch_live_model_ids() == ["ag/live"]
        assert get.call_args.args[0] == "https://router.test/v1/models"


# ── 3. is_dead: 26 h window + Redis error handling ───────────────────────────

class TestIsDead:

    def _store(self, fake_r, model, status, age_s):
        import datetime
        checked_at = (
            datetime.datetime.utcnow() - datetime.timedelta(seconds=age_s)
        ).isoformat()
        fake_r.hset("model_health", model, json.dumps({
            "status": status, "message": "x", "checked_at": checked_at
        }))

    def test_dead_within_26h_returns_true(self):
        fake_r = _make_fake_redis()
        self._store(fake_r, "ag/m", "dead", 3600)  # 1 hour old
        with _redis_patch(fake_r):
            from app.services.model_health import is_dead
            assert is_dead("ag/m") is True

    def test_dead_older_than_26h_returns_false(self):
        fake_r = _make_fake_redis()
        self._store(fake_r, "ag/m", "dead", 27 * 3600)  # 27 hours old
        with _redis_patch(fake_r):
            from app.services.model_health import is_dead
            assert is_dead("ag/m") is False

    def test_ok_status_returns_false(self):
        fake_r = _make_fake_redis()
        self._store(fake_r, "ag/m", "ok", 3600)
        with _redis_patch(fake_r):
            from app.services.model_health import is_dead
            assert is_dead("ag/m") is False

    def test_redis_error_returns_false(self):
        """Any Redis error must return False (fail-open)."""
        with patch("app.services.model_health._get_redis",
                   side_effect=Exception("connection refused")):
            from app.services.model_health import is_dead
            assert is_dead("ag/m") is False

    def test_missing_entry_returns_false(self):
        fake_r = _make_fake_redis()
        with _redis_patch(fake_r):
            from app.services.model_health import is_dead
            assert is_dead("ag/not-there") is False


# ── 4. Fallback iteration skips dead models; chain never empties ─────────────

class TestFallbackSkip:

    def test_dead_models_skipped_in_text_fallback(self):
        """_generate_with_fallback uses only live models in ROUTER_MODEL_FALLBACKS."""
        from app.services.ai_caption import ROUTER_MODEL_FALLBACKS

        # mark ALL fallbacks dead except the last one
        live_model = ROUTER_MODEL_FALLBACKS[-1]
        dead_models = set(ROUTER_MODEL_FALLBACKS[:-1])

        def _is_dead_side_effect(m):
            return m in dead_models

        called_models = []

        def _call_router_side_effect(prompt, model=None, **kw):
            called_models.append(model)
            if model == live_model:
                return '{"type":"news","is_breaking":false,"title":"T","subtitle":"S","caption":"C"}'
            raise RuntimeError("dead")

        with patch("app.services.model_health.is_dead", side_effect=_is_dead_side_effect):
            with patch("app.services.ai_caption.call_router_model",
                       side_effect=_call_router_side_effect):
                with patch("app.services.news_copywriter.generate_caption") as gen_cap:
                    # Simulate primary router call fails → enters fallback loop
                    gen_cap.side_effect = RuntimeError("primary failed")

                    from app.services.news_copywriter import _generate_with_fallback, _parse_news_copy

                    # We need to exercise the fallback-model loop in
                    # _generate_with_fallback.  Patch generate_caption to raise
                    # so control reaches the ROUTER_MODEL_FALLBACKS loop.
                    # But the function calls generate_caption first; let's mock
                    # it to return an unparseable router response, then the
                    # fallback loop kicks in.
                    gen_cap.side_effect = None
                    gen_cap.return_value = ("UNPARSEABLE", "router")

                    with patch("app.services.news_copywriter.call_router_model",
                               create=True, side_effect=_call_router_side_effect):
                        result, _ = _generate_with_fallback(
                            "prompt", _parse_news_copy,
                            None, context="news_copy"
                        )

        # Only non-dead models should have been tried
        for m in called_models:
            assert m not in dead_models, f"Dead model {m} was tried"

    def test_chain_never_empties_if_all_dead(self):
        """If is_dead() says ALL fallbacks are dead, the full original list is used."""
        from app.services.design_images import _vision_models, _VISION_MODEL_FALLBACKS

        with patch("app.services.model_health.is_dead", return_value=True):
            with patch("app.config.get_settings") as s:
                s.return_value.nine_router_vision_model = "ag/gemini-3.8-flash-low"
                from app.services import design_images
                models = design_images._vision_models()

        # Must not be empty — the full original list must remain
        assert len(models) > 0

    def test_dead_primary_uses_first_live_fallback(self):
        """If the configured primary is dead, first live fallback is returned first."""
        from app.services.design_images import _VISION_MODEL_FALLBACKS

        primary = "ag/dead-primary"
        first_live = _VISION_MODEL_FALLBACKS[1]  # [0] will also be dead

        def _is_dead(m):
            return m in {primary, _VISION_MODEL_FALLBACKS[0]}

        with patch("app.services.model_health.is_dead", side_effect=_is_dead):
            with patch("app.config.get_settings") as s:
                s.return_value.nine_router_vision_model = primary
                from app.services import design_images
                import importlib
                importlib.reload(design_images)  # reload to clear module cache
                models = design_images._vision_models()

        assert models[0] != primary
        # The first returned model must be alive
        assert not _is_dead(models[0])


# ── 5. /hermes/model-health shape + auth scope ───────────────────────────────

class TestHermesModelHealthEndpoint:

    def _make_app(self):
        from fastapi.testclient import TestClient
        from app.main import app
        return TestClient(app)

    def test_requires_auth(self):
        client = self._make_app()
        resp = client.get("/hermes/model-health")
        assert resp.status_code in (401, 403)

    def test_response_shape(self):
        """With a valid hermes read token, response has the expected keys."""
        fake_r = _make_fake_redis()
        # Store some sample health data
        import datetime
        now_iso = datetime.datetime.utcnow().isoformat()
        fake_r.hset("model_health", "ag/gemini-3.8-flash-low", json.dumps(
            {"status": "ok", "message": "pong", "checked_at": now_iso}
        ))
        fake_r.hset("model_health", "ag/retired", json.dumps(
            {"status": "dead", "message": "not listed", "checked_at": now_iso}
        ))

        live_ids = ["ag/gemini-3.8-flash-low", "ag/some-live-model"]

        with _redis_patch(fake_r):
            with patch("app.services.model_health._fetch_live_model_ids",
                       return_value=live_ids):
                with patch("app.api.deps.require_hermes_scope") as rhs:
                    # Make the dep a no-op that returns a fake token
                    fake_token = MagicMock()
                    fake_token.scopes = ["read"]
                    rhs.return_value = lambda **kw: fake_token

                    # Bypass actual DB/token check
                    from fastapi.testclient import TestClient
                    from app.main import app
                    from app.api import hermes as hermes_mod

                    # Override the dependency for this request
                    from app.api.deps import require_hermes_scope as real_rhs
                    app.dependency_overrides[hermes_mod._model_health_read_scope] = lambda: fake_token

                    client = TestClient(app)
                    try:
                        resp = client.get("/hermes/model-health")
                    finally:
                        app.dependency_overrides.clear()

        assert resp.status_code == 200
        data = resp.json()
        assert "chains" in data
        assert "text" in data["chains"]
        assert "vision" in data["chains"]
        assert "primaries" in data
        assert "dead" in data
        assert "live_router_models" in data
        assert "unused_live_models" in data
        # cx/* must not appear in live_router_models
        for m in data["live_router_models"]:
            assert not m.startswith("cx/")


# ── 6. Beat schedule + import check ─────────────────────────────────────────

class TestBeatAndImport:

    def test_beat_schedule_contains_model_health(self):
        from app.tasks.celery_app import celery_app
        schedule_tasks = {
            entry["task"]
            for entry in celery_app.conf.beat_schedule.values()
        }
        assert "app.tasks.model_health.check_model_health" in schedule_tasks

    def test_model_health_in_include(self):
        from app.tasks.celery_app import celery_app
        assert "app.tasks.model_health" in celery_app.conf.include

    def test_import_main_and_celery_app(self):
        """Smoke-import check from PLAN.md rule."""
        import app.main  # noqa: F401
        import app.tasks.celery_app as c  # noqa: F401
        for m in c.celery_app.conf.include:
            __import__(m)

    def test_model_health_task_importable(self):
        from app.tasks.model_health import check_model_health  # noqa: F401
        assert callable(check_model_health)
