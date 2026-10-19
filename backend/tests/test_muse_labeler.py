from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.api.labeler import ClaimRequest
from app.main import app
from app.services import api_tokens
from app.services import muse_labeler
from app.services.gallery_labeler import normalise_label_output


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
    assert "sprint" in schema["fields"]["context.session"]["allowed"]
    assert "sprint_qualifying" in schema["fields"]["event"]["fields"]["session"]["allowed"]
    assert schema["limits"]["claim"] == 50
    assert schema["limits"]["results"] == 50
    assert schema["limits"]["label"] == ["face", "action", "other"]
    assert schema["fields"]["keyword_mismatch"] == {"type": "boolean", "optional": True}
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
            "keyword_mismatch": False,
        }
    )

    assert result["shot_type"] == "other"
    assert result["source"] == "muse"
    assert result["schema_version"] == 1
    assert result["people"][0]["confidence"] == 0.8
    assert result["keyword_mismatch"] is False

    with pytest.raises(ValueError, match="people"):
        muse_labeler.normalise_result({"people": [{"name": "x", "confidence": 2}]})

    with pytest.raises(ValueError, match="event.season"):
        muse_labeler.normalise_result({"event": {"season": "not-a-season"}})


def test_claim_label_filter_accepts_allowed_values_and_rejects_unknown():
    for label in ("face", "action", "other"):
        assert ClaimRequest(limit=50, worker="muse", label=label).label == label
    with pytest.raises(ValueError):
        ClaimRequest(limit=50, worker="muse", label="portrait")


def test_keyword_mismatch_is_optional_and_stored_as_boolean():
    assert muse_labeler.normalise_result({"keyword_mismatch": True})["keyword_mismatch"] is True
    assert muse_labeler.normalise_result({})["keyword_mismatch"] is False


def test_keyword_guided_people_are_valid_without_caption_or_face_evidence():
    result = muse_labeler.normalise_result({
        "people": [{"name": "Max Verstappen", "confidence": 0.9}],
        "evidence": "gallery keyword + Red Bull livery",
    })
    assert result["people"] == [{"name": "Max Verstappen", "confidence": 0.9}]


@pytest.mark.parametrize("keyword, expected", [
    ("`max verstappen motogp", "Max Verstappen"),
    ("lebron james nba", "Lebron James"),
    ("  rafael nadal  ", "Rafael Nadal"),
    ("podium", None),
    ("f1", None),
])
def test_keyword_name_cleanup(keyword, expected):
    assert muse_labeler.keyword_name_from_gallery_keyword(keyword) == expected


def test_normalise_result_fills_people_from_keyword_unless_mismatched():
    result = muse_labeler.normalise_result({"people": [], "keyword_mismatch": False}, keyword="max verstappen motogp")
    assert result["people"] == [{"name": "Max Verstappen", "confidence": 0.8, "source": "keyword"}]

    mismatch = muse_labeler.normalise_result({"people": [], "keyword_mismatch": True}, keyword="max verstappen")
    assert mismatch["people"] == []


def test_stage_one_normaliser_fills_people_from_keyword():
    result = normalise_label_output(
        {"people": [], "keyword_mismatch": False, "keyword_person_visible": "yes"},
        keyword="max verstappen motogp",
    )
    assert result["people"] == [{"name": "Max Verstappen", "confidence": 0.8, "source": "keyword"}]

    unverified = normalise_label_output({"people": [], "keyword_mismatch": False}, keyword="max verstappen motogp")
    assert unverified["people"] == []


def test_stage_one_normaliser_skips_generic_keyword():
    result = normalise_label_output({"people": [], "keyword_mismatch": False}, keyword="podium")
    assert result["people"] == []


def test_captured_at_only_overwrites_with_higher_confidence():
    row = type("Row", (), {"captured_at": date(2026, 1, 2), "attributes": {"captured_at_confidence": "day"}})()

    muse_labeler.apply_captured_at(row, "2026-02-03T14:20:00Z", "estimated")
    assert row.captured_at == date(2026, 1, 2)

    muse_labeler.apply_captured_at(row, "2026-02-03T14:20:00Z", "exact")
    assert row.captured_at == date(2026, 2, 3)
    assert row.attributes["captured_at_confidence"] == "exact"


def test_archival_photo_forces_estimated_confidence_without_changing_date():
    row = type("Row", (), {"captured_at": date(2026, 1, 2), "attributes": {"captured_at_confidence": "day"}})()
    attrs = muse_labeler.normalise_result({"archival_photo": True})
    assert attrs["archival_photo"] is True
    muse_labeler.apply_archival_photo(row, attrs)
    assert row.captured_at == date(2026, 1, 2)
    assert row.attributes["captured_at_confidence"] == "estimated"
