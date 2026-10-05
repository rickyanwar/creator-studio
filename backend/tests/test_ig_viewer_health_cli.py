import json

from app.scripts import ig_viewer_health


def test_json_output_shape(monkeypatch, capsys):
    health = {
        "tiers": {"gramsnap": {"unhealthy": False}},
        "all_tiers_down": False,
        "generated_at": "2026-10-05T12:00:00+00:00",
    }
    monkeypatch.setattr(ig_viewer_health, "get_health", lambda: health)

    assert ig_viewer_health.main([]) == 0
    assert json.loads(capsys.readouterr().out) == health


def test_redis_error_is_json_and_exit_two(monkeypatch, capsys):
    def fail():
        raise ConnectionError("redis offline")

    monkeypatch.setattr(ig_viewer_health, "get_health", fail)

    assert ig_viewer_health.main([]) == 2
    assert json.loads(capsys.readouterr().out) == {"error": "health_unavailable"}
