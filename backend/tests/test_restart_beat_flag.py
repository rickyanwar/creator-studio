from datetime import datetime
from pathlib import Path
from unittest.mock import patch
import pytest
from fastapi import HTTPException

from app.api.jobs import RESTART_BEAT_FLAG, restart_beat


class FakeRedis:
    def __init__(self):
        self.calls = []

    def set(self, key, value, ex=None):
        self.calls.append({"key": key, "value": value, "ex": ex})


def test_restart_beat_flag_success():
    fake = FakeRedis()
    with patch("redis.from_url", return_value=fake):
        res = restart_beat(_=None)

    assert res == {"ok": True, "queued": True}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["key"] == "ops:restart_beat_requested"
    assert call["key"] == RESTART_BEAT_FLAG
    assert call["ex"] == 600
    parsed_dt = datetime.fromisoformat(call["value"])
    assert parsed_dt is not None


def test_restart_beat_flag_redis_error():
    with patch("redis.from_url", side_effect=Exception("Redis connection error")):
        with pytest.raises(HTTPException) as exc_info:
            restart_beat(_=None)
        assert exc_info.value.status_code == 500


def test_jobs_source_no_docker_from_env():
    import app.api.jobs as jobs_mod
    jobs_path = Path(jobs_mod.__file__).resolve()
    content = jobs_path.read_text(encoding="utf-8")
    assert "docker.from_env" not in content


def test_compose_no_docker_sock():
    compose_path = Path(__file__).resolve().parents[2] / "docker-compose.server.yml"
    assert compose_path.is_file()
    content = compose_path.read_text(encoding="utf-8")
    assert "/var/run/docker.sock" not in content
