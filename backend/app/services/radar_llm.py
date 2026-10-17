import hashlib
import json
import logging
import re
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

JUDGE_TIMEOUT_S = 20.0
MAX_MODELS_PER_CALL = 3
RUN_LLM_BUDGET_S = 60.0       # total AI time allowed per evaluate_radar run
RUN_MAX_CALLS = 15            # judge + classify calls per run (cache hits do not count)
CACHE_TTL_S = 7 * 24 * 3600
SHELF_KINDS = ("breaking", "news", "evergreen")


def _parse_json_dict(raw: str) -> dict:
    if not raw:
        raise ValueError("empty model response")
    cleaned = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:].strip()
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:].strip()
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3].strip()

    # Try non-greedy first {...}
    m = re.search(r"\{.*?\}", cleaned, flags=re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    # Try greedy {...}
    m2 = re.search(r"\{.*\}", cleaned, flags=re.DOTALL)
    if m2:
        parsed = json.loads(m2.group(0))
        if isinstance(parsed, dict):
            return parsed

    raise ValueError("no valid JSON object found in response")


def _cache_get(redis_client: Any, key: str) -> str | None:
    if not redis_client:
        return None
    try:
        val = redis_client.get(key)
        if isinstance(val, bytes):
            return val.decode("utf-8")
        return val
    except Exception as exc:
        logger.warning("RadarLlm cache get error: %s", type(exc).__name__)
        return None


def _cache_set(redis_client: Any, key: str, value: str, ttl: int) -> None:
    if not redis_client:
        return
    try:
        if hasattr(redis_client, "set"):
            try:
                redis_client.set(key, value, ex=ttl)
            except TypeError:
                if hasattr(redis_client, "setex"):
                    redis_client.setex(key, ttl, value)
                else:
                    redis_client.set(key, value)
        elif hasattr(redis_client, "setex"):
            redis_client.setex(key, ttl, value)
    except Exception as exc:
        logger.warning("RadarLlm cache set error: %s", type(exc).__name__)


class RadarLlm:
    def __init__(
        self,
        redis_client: Any,
        call_model: Callable[[str, str], str] | None = None,
        clock: Callable[[], float] | None = None,
        models: list[str] | None = None,
    ):
        self.redis = redis_client
        self.clock = clock if clock is not None else time.monotonic
        self.started = self.clock()
        self.calls = 0

        if call_model is not None:
            self.call_model = call_model
        else:
            from app.services import ai_caption
            self.call_model = lambda prompt, model: ai_caption.call_router_model(
                prompt, model, timeout=JUDGE_TIMEOUT_S, temperature=0
            )

        if models is not None:
            self.models = list(models)
        else:
            from app.services import ai_caption
            from app.services import model_health

            seen = set()
            deduped = []
            for m in ai_caption.ROUTER_MODEL_FALLBACKS:
                if m not in seen:
                    seen.add(m)
                    deduped.append(m)
            alive = [m for m in deduped if not model_health.is_dead(m)]
            self.models = alive if alive else deduped

    def _ask(self, prompt: str) -> dict | None:
        from app.services import nine_router

        if not nine_router.get_nine_router_config().enabled:
            return None

        if self.calls >= RUN_MAX_CALLS or (self.clock() - self.started) > RUN_LLM_BUDGET_S:
            return None

        self.calls += 1

        for model in self.models[:MAX_MODELS_PER_CALL]:
            # evaluate_radar holds a 180 s lock: never start another model once the run budget is spent.
            if (self.clock() - self.started) > RUN_LLM_BUDGET_S:
                break
            try:
                raw = self.call_model(prompt, model)
                return _parse_json_dict(raw)
            except Exception as exc:
                logger.warning("RadarLlm model %s failed: %s", model, type(exc).__name__)
                continue

        return None

    def judge_same_story(self, a: str, b: str) -> bool:
        sorted_texts = sorted([a or "", b or ""])
        key_hash = hashlib.sha1("\x00".join(sorted_texts).encode("utf-8")).hexdigest()
        cache_key = f"radar:judge:{key_hash}"

        cached = _cache_get(self.redis, cache_key)
        if cached == "1":
            return True
        if cached == "0":
            return False

        prompt = (
            "Two Instagram captions from motorsport/sport news accounts. Do they report the SAME specific story "
            "(same event, same people, same news), not just the same topic? Reply ONLY JSON {\"same_story\": true|false}.\n\n"
            f"Caption 1:\n{(a or '')[:600]}\n\n"
            f"Caption 2:\n{(b or '')[:600]}"
        )

        d = self._ask(prompt)
        if d is None:
            return False

        ans = d.get("same_story") is True
        _cache_set(self.redis, cache_key, "1" if ans else "0", CACHE_TTL_S)
        return ans

    def classify_shelf(self, text: str) -> str:
        key_hash = hashlib.sha1((text or "").encode("utf-8")).hexdigest()
        cache_key = f"radar:shelf:{key_hash}"

        cached = _cache_get(self.redis, cache_key)
        if cached in SHELF_KINDS:
            return cached

        prompt = (
            'Classify this sport news caption: "breaking" = urgent news that is old by tomorrow (results, crashes, signings announced today); '
            '"news" = normal news relevant for a few days; "evergreen" = timeless (history, explainers, features, trivia). '
            'Reply ONLY JSON {"shelf": "breaking|news|evergreen"}.\n\n'
            f"Caption:\n{(text or '')[:800]}"
        )

        d = self._ask(prompt)
        if d is None:
            return "news"

        shelf = d.get("shelf")
        if isinstance(shelf, str):
            shelf = shelf.strip().lower()

        if shelf in SHELF_KINDS:
            _cache_set(self.redis, cache_key, shelf, CACHE_TTL_S)
            return shelf

        return "news"
