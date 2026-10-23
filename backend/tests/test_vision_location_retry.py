"""Owner 2026-10-24: a relay "User location is not supported" error is retried briefly; other errors are not."""
from types import SimpleNamespace

import app.services.design_images as di


class _FakeCompletions:
    def __init__(self, script):
        self.script = list(script)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.script.pop(0) if self.script else RuntimeError("no more answers")
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=item))])


def _patch(monkeypatch, script, models=("m1",)):
    fake = _FakeCompletions(script)
    client = SimpleNamespace(chat=SimpleNamespace(completions=fake))
    import openai
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: client)
    monkeypatch.setattr(di, "_vision_models", lambda: list(models))
    monkeypatch.setattr("app.services.nine_router.get_nine_router_config", lambda: SimpleNamespace(base_url="http://x", api_key="k"))
    monkeypatch.setattr("app.services.ai_caption.log_ai_copy_event", lambda **kw: None)
    sleeps = []
    monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
    return fake, sleeps


LOCATION = RuntimeError("Error code: 400 - User location is not supported for the API use")


def test_location_error_is_retried_and_recovers(monkeypatch):
    fake, sleeps = _patch(monkeypatch, [LOCATION, "ok"])
    assert di._vision_chat([{"type": "text", "text": "hi"}]) == "ok"
    assert sleeps == [2.0] and fake.calls == 2


def test_location_error_gives_up_after_two_short_waits(monkeypatch):
    fake, sleeps = _patch(monkeypatch, [LOCATION, LOCATION, LOCATION])
    try:
        di._vision_chat([{"type": "text", "text": "hi"}])
        raise AssertionError("expected an error")
    except RuntimeError as exc:
        assert "location" in str(exc)
    assert sleeps == [2.0, 4.0] and fake.calls == 3


def test_other_errors_are_not_retried(monkeypatch):
    fake, sleeps = _patch(monkeypatch, [RuntimeError("timeout"), "never"])
    try:
        di._vision_chat([{"type": "text", "text": "hi"}])
        raise AssertionError("expected an error")
    except RuntimeError as exc:
        assert "timeout" in str(exc)
    assert sleeps == [] and fake.calls == 1
