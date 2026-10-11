from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.services import api_tokens
from app.services import muse_labeler


class FakeRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)

    def get(self, key):
        return self.values.get(key)


def test_schema_lists_enums_and_limits():
    schema = muse_labeler.label_schema()

    assert schema["version"] == 1
    assert "shot_type" in schema["fields"]
    assert "car_on_track" in schema["fields"]["shot_type"]["allowed"]
    assert "basketball" in schema["fields"]["sport"]["allowed"]
    assert "court" in schema["fields"]["venue"]["fields"]["type"]["allowed"]
    assert schema["fields"]["event"]["fields"]["season"] == "YYYY or YYYY-YY"
    assert schema["limits"] == {"claim": 50, "results": 50}
    assert "never guess names" in schema["guidelines"].lower()


def test_leasing_is_atomic_and_release_only_accepts_owner():
    redis = FakeRedis()

    assert muse_labeler.acquire_lease(redis, 7, "muse") is True
    assert muse_labeler.acquire_lease(redis, 7, "other") is False
    assert muse_labeler.release_lease(redis, 7, "other") is False
    assert muse_labeler.release_lease(redis, 7, "muse") is True
    assert muse_labeler.acquire_lease(redis, 7, "other") is True


def test_normalise_result_merges_muse_metadata_and_rejects_invalid_metadata():
    result = muse_labeler.normalise_result(
        {
            "shot_type": "not-a-shot",
            "people_count": 1,
            "activity": "driving",
            "expression": "focused",
            "context": {"session": "race"},
            "people": [{"name": "Rider", "confidence": 0.8}],
            "event": {"series": "MotoGP", "event_name": "GP", "year": 2026, "session": "race"},
            "evidence": "visible paddock sign",
        }
    )

    assert result["shot_type"] == "other"
    assert result["source"] == "muse"
    assert result["schema_version"] == 1
    assert result["people"][0]["confidence"] == 0.8

    with pytest.raises(ValueError, match="people"):
        muse_labeler.normalise_result({"people": [{"name": "x", "confidence": 2}]})


def test_captured_at_only_overwrites_with_higher_confidence():
    row = type("Row", (), {"captured_at": date(2026, 1, 2), "attributes": {"captured_at_confidence": "day"}})()

    muse_labeler.apply_captured_at(row, "2026-02-03T14:20:00Z", "estimated")
    assert row.captured_at == date(2026, 1, 2)

    muse_labeler.apply_captured_at(row, "2026-02-03T14:20:00Z", "exact")
    assert row.captured_at == date(2026, 2, 3)
    assert row.attributes["captured_at_confidence"] == "exact"
