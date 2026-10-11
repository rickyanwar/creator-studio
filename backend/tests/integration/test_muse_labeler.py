import pytest
from datetime import date, datetime, timezone

from app.models.gallery import GalleryImage
from app.api.labeler import stats
from app.services import muse_labeler


class LeaseRedis:
    def __init__(self):
        self.values = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)

    def scan_iter(self, match=None):
        for key in list(self.values):
            if match is None or key.startswith(match.rstrip("*")):
                yield key


def _image(image_id, downloaded_at):
    return GalleryImage(
        id=image_id,
        keyword="fresh",
        extra_keywords=[],
        source_image_url=f"https://source.test/{image_id}",
        local_path=f"/tmp/{image_id}.jpg",
        public_url=f"https://public.test/{image_id}.jpg",
        width=1000,
        height=800,
        source_engine="bing",
        downloaded_at=downloaded_at,
        is_deleted=False,
        label_attempts=0,
    )


def test_claim_skips_150_leased_top_candidates(db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [_image(10000 + index, now) for index in range(200)]
    db_session.add_all(rows)
    db_session.commit()
    redis = LeaseRedis()
    for row in rows[:150]:
        assert muse_labeler.acquire_lease(redis, row.id, "muse")

    claimed = muse_labeler.claim_images(db_session, 50, "muse", redis)

    assert len(claimed) == 50
    assert {item["id"] for item in claimed}.isdisjoint({row.id for row in rows[:150]})


def test_concurrent_back_to_back_claims_are_disjoint(db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [_image(10300 + index, now) for index in range(120)]
    db_session.add_all(rows)
    db_session.commit()
    redis = LeaseRedis()

    first = muse_labeler.claim_images(db_session, 50, "muse", redis)
    second = muse_labeler.claim_images(db_session, 50, "muse", redis)

    assert len(first) == len(second) == 50
    assert {item["id"] for item in first}.isdisjoint({item["id"] for item in second})


def test_claim_returns_empty_when_everything_is_leased(db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [_image(10500 + index, now) for index in range(20)]
    for row in rows:
        row.label = "face"
    db_session.add_all(rows)
    db_session.commit()
    redis = LeaseRedis()
    for row in rows:
        assert muse_labeler.acquire_lease(redis, row.id, "muse")

    assert muse_labeler.claim_images(db_session, 50, "muse", redis, label="face") == []


def test_claim_label_filter_restricts_candidates(db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    face = _image(10600, now)
    face.label = "face"
    action = _image(10601, now)
    action.label = "action"
    db_session.add_all([face, action])
    db_session.commit()
    redis = LeaseRedis()

    claimed = muse_labeler.claim_images(db_session, 50, "muse", redis, label="face")

    assert [item["id"] for item in claimed] == [face.id]


def test_date_only_attributes_are_claimable_and_muse_result_merges_metadata(db_session):
    row = _image(10602, datetime.now(timezone.utc).replace(tzinfo=None))
    row.keyword = "max verstappen f1"
    row.captured_at = date(2026, 1, 2)
    row.attributes = {
        "captured_at_source": "getty_id",
        "captured_at_confidence": "day",
        "getty_hints": {"asset_id": "abc"},
        "people": [{"name": "Keyword Person", "confidence": 0.8, "source": "keyword"}],
    }
    db_session.add(row)
    db_session.commit()
    redis = LeaseRedis()

    assert [item["id"] for item in muse_labeler.claim_images(db_session, 50, "muse", redis)] == [row.id]

    result = muse_labeler.process_results(
        db_session,
        {"results": [{"id": row.id, "shot_type": "portrait", "people": [], "keyword_mismatch": False}]},
        "muse",
        redis,
    )

    assert result["saved"] == 1
    db_session.refresh(row)
    assert row.labeled_at is not None
    assert row.attributes["captured_at_source"] == "getty_id"
    assert row.attributes["captured_at_confidence"] == "day"
    assert row.attributes["getty_hints"] == {"asset_id": "abc"}
    assert row.attributes["people"] == [{"name": "Keyword Person", "confidence": 0.8, "source": "keyword"}]


def test_stats_use_labeled_at_not_attributes(db_session, monkeypatch):
    remaining = _image(10603, datetime.now(timezone.utc).replace(tzinfo=None))
    remaining.attributes = {"captured_at_source": "getty_id"}
    labeled = _image(10604, datetime.now(timezone.utc).replace(tzinfo=None))
    labeled.attributes = {"source": "muse"}
    labeled.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.add_all([remaining, labeled])
    db_session.commit()
    redis = LeaseRedis()
    monkeypatch.setattr(muse_labeler, "redis_client", lambda: redis)

    before = stats(db_session, token=None)
    assert before["labeled"] == 1
    assert before["remaining"] == 1

    remaining.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    db_session.commit()
    after = stats(db_session, token=None)
    assert after["labeled"] == 2
    assert after["remaining"] == 0


def test_muse_keyword_mismatch_drops_keyword_people(db_session):
    row = _image(10605, datetime.now(timezone.utc).replace(tzinfo=None))
    row.keyword = "max verstappen f1"
    row.attributes = {
        "people": [{"name": "Max Verstappen", "confidence": 0.8, "source": "keyword"}],
    }
    db_session.add(row)
    db_session.commit()
    redis = LeaseRedis()

    result = muse_labeler.process_results(
        db_session,
        {"results": [{"id": row.id, "people": [], "keyword_mismatch": True}]},
        "muse",
        redis,
    )

    assert result["saved"] == 1
    db_session.refresh(row)
    assert row.attributes["keyword_mismatch"] is True
    assert row.attributes["people"] == []


def test_fill_people_from_keyword_is_idempotent_and_skips_mismatch(db_session):
    first = _image(10700, datetime.now(timezone.utc).replace(tzinfo=None))
    first.keyword = "max verstappen f1"
    first.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    first.attributes = {"people": [], "keyword_mismatch": False}
    mismatch = _image(10701, datetime.now(timezone.utc).replace(tzinfo=None))
    mismatch.keyword = "lebron james nba"
    mismatch.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    mismatch.attributes = {"people": [], "keyword_mismatch": True}
    db_session.add_all([first, mismatch])
    db_session.commit()

    assert muse_labeler.fill_people_from_keyword(db_session) == 1
    db_session.refresh(first)
    assert first.attributes["people"] == [{"name": "Max Verstappen", "confidence": 0.8, "source": "keyword"}]
    assert muse_labeler.fill_people_from_keyword(db_session) == 0


def test_fill_people_from_keyword_dry_run_does_not_write(db_session):
    row = _image(10702, datetime.now(timezone.utc).replace(tzinfo=None))
    row.keyword = "max verstappen f1"
    row.labeled_at = datetime.now(timezone.utc).replace(tzinfo=None)
    row.attributes = {"people": [], "keyword_mismatch": False}
    db_session.add(row)
    db_session.commit()

    assert muse_labeler.fill_people_from_keyword(db_session, dry_run=True) == 1
    db_session.refresh(row)
    assert row.attributes["people"] == []
