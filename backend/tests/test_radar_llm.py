import pytest
from unittest.mock import MagicMock

from app.services.radar_llm import (
    RadarLlm,
    JUDGE_TIMEOUT_S,
    MAX_MODELS_PER_CALL,
    RUN_LLM_BUDGET_S,
    RUN_MAX_CALLS,
    CACHE_TTL_S,
    SHELF_KINDS,
)


class FakeRedis:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = str(value)

    def setex(self, key, time, value):
        self.data[key] = str(value)


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, delta):
        self.now += delta


def test_judge_same_story_json_and_cache(monkeypatch):
    """4a. judge True / False parsed from JSON (also inside ```json fences);
    result cached (second call does not call the model)."""
    monkeypatch.setattr(
        "app.services.nine_router.get_nine_router_config",
        lambda: MagicMock(enabled=True),
    )
    redis_mock = FakeRedis()
    calls = []

    def fake_call(prompt, model):
        calls.append((prompt, model))
        if "story 1" in prompt:
            return '{"same_story": true}'
        return '```json\n{"same_story": false}\n```'

    llm = RadarLlm(redis_client=redis_mock, call_model=fake_call, models=["test-model"])

    # First call -> True parsed from raw JSON
    res1 = llm.judge_same_story("story 1 text", "other text")
    assert res1 is True
    assert len(calls) == 1

    # Second call with same captions (reversed order) -> cached, model not called
    res2 = llm.judge_same_story("other text", "story 1 text")
    assert res2 is True
    assert len(calls) == 1

    # Call with different text -> False parsed from fenced JSON
    res3 = llm.judge_same_story("story 2 text", "unrelated text")
    assert res3 is False
    assert len(calls) == 2

    # Cached False call -> cached, model not called
    res4 = llm.judge_same_story("story 2 text", "unrelated text")
    assert res4 is False
    assert len(calls) == 2


def test_fallback_models_and_max_models_cap(monkeypatch):
    """4b. first model raises, second answers -> uses the second;
    never more than MAX_MODELS_PER_CALL models per call."""
    monkeypatch.setattr(
        "app.services.nine_router.get_nine_router_config",
        lambda: MagicMock(enabled=True),
    )
    redis_mock = FakeRedis()
    calls = []

    def fake_call(prompt, model):
        calls.append(model)
        if model == "m1":
            raise RuntimeError("m1 timeout")
        if model == "m2":
            return '{"same_story": true}'
        raise RuntimeError("other model failure")

    llm = RadarLlm(
        redis_client=redis_mock,
        call_model=fake_call,
        models=["m1", "m2", "m3", "m4", "m5"],
    )

    ans = llm.judge_same_story("caption A", "caption B")
    assert ans is True
    assert calls == ["m1", "m2"]

    # When all models raise, never tries more than MAX_MODELS_PER_CALL (3)
    all_fail_calls = []

    def fake_fail_call(prompt, model):
        all_fail_calls.append(model)
        raise RuntimeError(f"{model} failed")

    llm_fail = RadarLlm(
        redis_client=redis_mock,
        call_model=fake_fail_call,
        models=["m1", "m2", "m3", "m4", "m5"],
    )

    ans_fail = llm_fail.judge_same_story("new A", "new B")
    assert ans_fail is False
    assert len(all_fail_calls) == MAX_MODELS_PER_CALL
    assert all_fail_calls == ["m1", "m2", "m3"]


def test_router_disabled(monkeypatch):
    """4c. router disabled (monkeypatch get_nine_router_config to an object
    with enabled=False) -> judge False, shelf 'news', model not called."""
    monkeypatch.setattr(
        "app.services.nine_router.get_nine_router_config",
        lambda: MagicMock(enabled=False),
    )
    redis_mock = FakeRedis()
    call_model = MagicMock()

    llm = RadarLlm(redis_client=redis_mock, call_model=call_model, models=["test-model"])

    assert llm.judge_same_story("text a", "text b") is False
    assert llm.classify_shelf("some caption") == "news"
    call_model.assert_not_called()


def test_budget_exhaustion(monkeypatch):
    """4d. budget: after RUN_MAX_CALLS calls -> returns defaults without calling;
    clock past RUN_LLM_BUDGET_S -> defaults."""
    monkeypatch.setattr(
        "app.services.nine_router.get_nine_router_config",
        lambda: MagicMock(enabled=True),
    )
    redis_mock = FakeRedis()
    calls = []

    def fake_call(prompt, model):
        calls.append(prompt)
        return '{"same_story": true, "shelf": "breaking"}'

    clock = FakeClock(start=1000.0)
    llm = RadarLlm(
        redis_client=redis_mock,
        call_model=fake_call,
        clock=clock,
        models=["m1"],
    )

    # Exhaust RUN_MAX_CALLS (15) calls
    for i in range(RUN_MAX_CALLS):
        res = llm.judge_same_story(f"text_{i}_a", f"text_{i}_b")
        assert res is True

    assert len(calls) == RUN_MAX_CALLS
    assert llm.calls == RUN_MAX_CALLS

    # 16th call -> budget exceeded, returns default False without calling
    res_exceeded = llm.judge_same_story("overflow_a", "overflow_b")
    assert res_exceeded is False
    assert len(calls) == RUN_MAX_CALLS

    # shelf also returns default "news" without calling
    shelf_exceeded = llm.classify_shelf("overflow caption")
    assert shelf_exceeded == "news"
    assert len(calls) == RUN_MAX_CALLS

    # Test clock budget expiration
    clock2 = FakeClock(start=1000.0)
    calls2 = []
    llm2 = RadarLlm(
        redis_client=FakeRedis(),
        call_model=lambda p, m: calls2.append(p) or '{"same_story": true}',
        clock=clock2,
        models=["m1"],
    )
    # Advance clock beyond RUN_LLM_BUDGET_S (60s)
    clock2.advance(RUN_LLM_BUDGET_S + 1.0)
    assert llm2.judge_same_story("time_a", "time_b") is False
    assert len(calls2) == 0


def test_classify_shelf_validation_and_caching(monkeypatch):
    """4e. classify_shelf invalid value ('urgent') -> 'news' and not cached;
    valid 'evergreen' cached."""
    monkeypatch.setattr(
        "app.services.nine_router.get_nine_router_config",
        lambda: MagicMock(enabled=True),
    )
    redis_mock = FakeRedis()
    call_count = [0]

    def fake_call(prompt, model):
        call_count[0] += 1
        if "invalid" in prompt:
            return '{"shelf": "urgent"}'
        return '{"shelf": "evergreen"}'

    llm = RadarLlm(redis_client=redis_mock, call_model=fake_call, models=["m1"])

    # Invalid value "urgent" -> defaults to "news", NOT cached
    res_invalid = llm.classify_shelf("invalid story caption")
    assert res_invalid == "news"
    assert call_count[0] == 1

    # Second call with same caption -> model called again because not cached
    res_invalid2 = llm.classify_shelf("invalid story caption")
    assert res_invalid2 == "news"
    assert call_count[0] == 2

    # Valid value "evergreen" -> cached
    res_valid = llm.classify_shelf("history of grand prix")
    assert res_valid == "evergreen"
    assert call_count[0] == 3

    # Second call with same caption -> returned from cache, model not called
    res_valid2 = llm.classify_shelf("history of grand prix")
    assert res_valid2 == "evergreen"
    assert call_count[0] == 3


def test_evaluate_radar_no_mocks():
    """4f. evaluate_radar no longer references mocks."""
    import app.tasks.radar as t
    assert not hasattr(t, "mock_judge_same_story")
    assert not hasattr(t, "mock_classify_shelf")


def test_models_default_dedup_and_dead_filtering(monkeypatch):
    """Test default models deduplication and model_health.is_dead filtering."""
    monkeypatch.setattr(
        "app.services.ai_caption.ROUTER_MODEL_FALLBACKS",
        ["model-a", "model-b", "model-a", "model-dead"],
    )
    monkeypatch.setattr(
        "app.services.model_health.is_dead",
        lambda m: m == "model-dead",
    )
    llm = RadarLlm(redis_client=FakeRedis())
    assert llm.models == ["model-a", "model-b"]

    # If all dead, keep full deduped list
    monkeypatch.setattr(
        "app.services.model_health.is_dead",
        lambda m: True,
    )
    llm_all_dead = RadarLlm(redis_client=FakeRedis())
    assert llm_all_dead.models == ["model-a", "model-b", "model-dead"]
