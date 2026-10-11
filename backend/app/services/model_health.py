"""Model health service — dead-model self-protection.

Probes every configured 9Router model ID and caches results in Redis so the
fallback chains can skip dead models without paying a network round-trip per
request.

Statuses:
  ok          — responded within 40 s with a non-empty reply
  dead        — 404 / NOT_FOUND / "Requested entity was not found"
  unavailable — 429 / 5xx / quota ("reset after", "Unavailable", rate limit)
  error       — anything else

Hard rules (owner rules, PLAN.md):
  - NEVER probe or store cx/* model IDs.
  - Only probe IDs present on the live /v1/models list.
  - Unlisted configured IDs are immediately marked dead("not listed").
"""

import datetime
import json
import logging
import time

import httpx
import redis as _redis

from app.config import get_settings
from app.services.nine_router import get_nine_router_config

logger = logging.getLogger(__name__)

_REDIS_HASH = "model_health"
_REDIS_TTL_S = 26 * 3600  # 26 hours
_PROBE_TIMEOUT_S = 40.0
_INTER_PROBE_PAUSE_S = 0.5


# ── internal helpers ─────────────────────────────────────────────────────────

def _get_redis() -> _redis.Redis:
    return _redis.from_url(get_settings().redis_url, decode_responses=True)


def _get_nine_router_config():
    return get_nine_router_config()


def _classify_error(message: str) -> tuple[str, str]:
    """Classify an error string into (status, message)."""
    lo = message.lower()
    if (
        "404" in lo
        or "not_found" in lo
        or "requested entity was not found" in lo
    ):
        return "dead", message[:200]
    if (
        "429" in lo
        or any(str(code) in lo for code in range(500, 600))
        or "reset after" in lo
        or "unavailable" in lo
        or "rate limit" in lo
        or "quota" in lo
    ):
        return "unavailable", message[:200]
    return "error", message[:200]


def _fetch_live_model_ids() -> list[str]:
    """GET {base}/v1/models and return all model ids, excluding cx/*."""
    cfg = _get_nine_router_config()
    if not cfg.enabled:
        return []
    try:
        models_url = (
            f"{cfg.base_url}/models"
            if cfg.base_url.rstrip("/").endswith("/v1")
            else f"{cfg.base_url}/v1/models"
        )
        resp = httpx.get(
            models_url,
            headers={"Authorization": f"Bearer {cfg.api_key or 'sk-9router'}"},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        ids = [m["id"] for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
        return [i for i in ids if not i.startswith("cx/")]
    except Exception as exc:
        logger.warning("model_health: could not fetch /v1/models: %s", exc)
        return []


# ── public API ───────────────────────────────────────────────────────────────

def configured_models() -> list[str]:
    """Union of all configured model IDs (text + vision chains + primaries),
    with cx/* IDs removed."""
    from app.services.ai_caption import ROUTER_MODEL_FALLBACKS
    from app.services.design_images import _VISION_MODEL_FALLBACKS

    cfg = _get_nine_router_config()
    s = get_settings()

    primaries = [
        m for m in [
            cfg.model,
            cfg.discussion_model,
            s.nine_router_vision_model,
        ] if m
    ]

    all_ids = primaries + list(ROUTER_MODEL_FALLBACKS) + list(_VISION_MODEL_FALLBACKS)
    seen: set[str] = set()
    result: list[str] = []
    for m in all_ids:
        if m and not m.startswith("cx/") and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def probe(model: str) -> tuple[str, str]:
    """POST a tiny chat completion to model. Returns (status, message).

    Never call cx/* — callers must filter before calling this.
    """
    assert not model.startswith("cx/"), f"probe() must never be called with cx/* model: {model}"

    cfg = _get_nine_router_config()
    if not cfg.enabled:
        return "error", "9Router not configured"

    url = f"{cfg.base_url}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key or 'sk-9router'}"}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 5,
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=_PROBE_TIMEOUT_S)
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        content = ""
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
        if not content:
            return _classify_error("empty response from model")
        return "ok", content[:200]
    except httpx.HTTPStatusError as exc:
        msg = f"{exc.response.status_code} {exc.response.text[:200]}"
        return _classify_error(msg)
    except Exception as exc:
        return _classify_error(str(exc))


def check_all() -> None:
    """Probe all configured models; store results in Redis with 26 h TTL.

    - Fetches /v1/models first; configured IDs not on the live list are
      immediately marked dead("not listed") without a network probe.
    - cx/* IDs are silently skipped.
    - Probes sequentially with 0.5 s pauses.
    """
    live_ids = set(_fetch_live_model_ids())
    models = configured_models()

    r = _get_redis()
    for model in models:
        if model.startswith("cx/"):
            continue  # hard rule: never touch cx/*

        now_iso = datetime.datetime.utcnow().isoformat()

        if model not in live_ids:
            status, message = "dead", "not listed on /v1/models"
        else:
            status, message = probe(model)
            time.sleep(_INTER_PROBE_PAUSE_S)

        entry = json.dumps({"status": status, "message": message, "checked_at": now_iso})
        r.hset(_REDIS_HASH, model, entry)

    r.expire(_REDIS_HASH, _REDIS_TTL_S)


def is_dead(model: str) -> bool:
    """Return True only if Redis records this model as dead and the record is
    < 26 h old.  Any Redis error → False (fail-open: prefer a slow call over
    silently emptying the fallback chain)."""
    try:
        r = _get_redis()
        raw = r.hget(_REDIS_HASH, model)
        if not raw:
            return False
        data = json.loads(raw)
        if data.get("status") != "dead":
            return False
        checked_at_str = data.get("checked_at", "")
        if not checked_at_str:
            return False
        checked_at = datetime.datetime.fromisoformat(checked_at_str)
        age_s = (datetime.datetime.utcnow() - checked_at).total_seconds()
        return age_s < _REDIS_TTL_S
    except Exception as exc:
        logger.debug("model_health.is_dead(%s) Redis error (fail-open): %s", model, exc)
        return False


def get_health_snapshot(
    live_ids: list[str] | None = None,
) -> dict:
    """Read current health from Redis + one /v1/models call (no probing).

    Used by GET /hermes/model-health — never triggers probes.
    """
    from app.services.ai_caption import ROUTER_MODEL_FALLBACKS
    from app.services.design_images import _VISION_MODEL_FALLBACKS

    cfg = _get_nine_router_config()
    s = get_settings()

    if live_ids is None:
        live_ids = _fetch_live_model_ids()  # already excludes cx/*

    live_set = set(live_ids)

    # Read stored health records
    try:
        r = _get_redis()
        stored = r.hgetall(_REDIS_HASH)
    except Exception:
        stored = {}

    def _entry(model: str) -> dict:
        raw = stored.get(model)
        if raw:
            try:
                d = json.loads(raw)
                return {
                    "model": model,
                    "status": d.get("status", "unknown"),
                    "checked_at": d.get("checked_at"),
                    "message": d.get("message"),
                }
            except Exception:
                pass
        return {"model": model, "status": "unknown", "checked_at": None, "message": None}

    text_primaries = [m for m in [cfg.model, cfg.discussion_model] if m and not m.startswith("cx/")]
    vision_primary = s.nine_router_vision_model
    if vision_primary and vision_primary.startswith("cx/"):
        vision_primary = None
    text_fallbacks = [m for m in ROUTER_MODEL_FALLBACKS if not m.startswith("cx/")]
    vision_fallbacks = [m for m in _VISION_MODEL_FALLBACKS if not m.startswith("cx/")]

    text_chain_models: list[str] = []
    seen: set[str] = set()
    for m in text_primaries + text_fallbacks:
        if m and m not in seen:
            seen.add(m)
            text_chain_models.append(m)

    vision_chain_models: list[str] = []
    seen2: set[str] = set()
    for m in [vision_primary] + vision_fallbacks:
        if m and m not in seen2:
            seen2.add(m)
            vision_chain_models.append(m)

    all_chain_models = set(text_chain_models) | set(vision_chain_models)
    # ag/* live models not in any chain
    unused_live = [m for m in live_ids if m.startswith("ag/") and m not in all_chain_models]

    dead_list = [
        m for m in all_chain_models
        if is_dead(m)
    ]

    return {
        "chains": {
            "text": [_entry(m) for m in text_chain_models],
            "vision": [_entry(m) for m in vision_chain_models],
        },
        "primaries": {
            "text": _entry(cfg.model) if cfg.model else None,
            "discussion": _entry(cfg.discussion_model) if cfg.discussion_model else None,
            "vision": _entry(vision_primary) if vision_primary else None,
        },
        "dead": dead_list,
        "live_router_models": live_ids,
        "unused_live_models": unused_live,
    }
