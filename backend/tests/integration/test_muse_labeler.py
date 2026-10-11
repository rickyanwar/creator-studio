import pytest
from datetime import datetime, timezone

from app.models.gallery import GalleryImage
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


def test_fill_people_from_keyword_is_idempotent_and_skips_mismatch(db_session):
    first = _image(10700, datetime.now(timezone.utc).replace(tzinfo=None))
    first.keyword = "max verstappen f1"
    first.attributes = {"people": [], "keyword_mismatch": False}
    mismatch = _image(10701, datetime.now(timezone.utc).replace(tzinfo=None))
    mismatch.keyword = "lebron james nba"
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
    row.attributes = {"people": [], "keyword_mismatch": False}
    db_session.add(row)
    db_session.commit()

    assert muse_labeler.fill_people_from_keyword(db_session, dry_run=True) == 1
    db_session.refresh(row)
    assert row.attributes["people"] == []
