import threading
import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from app.models.gallery import GalleryImage
from app.models.settings import Settings
from app.services.gallery_labeler import LabelerUnavailable
from app.tasks.gallery_labels import label_gallery_image, label_new_gallery_images
from app.services.gallery_label_budget import reserve, release, _get_today_key
from app.database import Base, engine, SessionLocal


class FakeBudgetRedis:
    def __init__(self):
        self._data = {}
        self._ttl = {}
        self._lock = threading.Lock()

    def get(self, key):
        with self._lock:
            val = self._data.get(key)
            return str(val) if val is not None else None

    def set(self, key, value, nx=False, ex=None):
        with self._lock:
            if nx and key in self._data:
                return False
            self._data[key] = int(value)
            if ex:
                self._ttl[key] = ex
            return True

    def incr(self, key):
        with self._lock:
            val = int(self._data.get(key, 0)) + 1
            self._data[key] = val
            return val

    def decr(self, key):
        with self._lock:
            val = int(self._data.get(key, 0)) - 1
            self._data[key] = val
            return val

    def expire(self, key, seconds):
        with self._lock:
            self._ttl[key] = seconds
            return True


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.query(GalleryImage).filter(GalleryImage.source_image_url.like("http://test-%")).delete(synchronize_session=False)
        session.commit()
        session.close()


def _unique_url(prefix: str) -> str:
    return f"http://test-{prefix}-{uuid.uuid4().hex}"


def test_concurrent_reserve_cap_strictly_enforced():
    """a cap of 2 with 5 concurrent reserve() calls -> exactly 2 succeed."""
    r = FakeBudgetRedis()
    cap = 2
    results = []

    def call_reserve():
        ok = reserve(cap=cap, redis_client=r)
        results.append(ok)

    threads = [threading.Thread(target=call_reserve) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert results.count(True) == 2
    assert results.count(False) == 3
    # Counter should end back at 2 (not overshot)
    today_key = _get_today_key()
    assert int(r.get(today_key)) == 2


def test_skip_releases_slot():
    """a skip after reservation releases the slot."""
    r = FakeBudgetRedis()
    cap = 1
    assert reserve(cap=cap, redis_client=r) is True
    # At cap, next reserve fails
    assert reserve(cap=cap, redis_client=r) is False

    # Skip happened, release slot
    release(redis_client=r)

    # Slot now available again
    assert reserve(cap=cap, redis_client=r) is True


def test_seeding_from_db_count(db_session):
    """seeding from the DB count works."""
    r = FakeBudgetRedis()
    now = datetime.now(timezone.utc)
    today_start = datetime(now.year, now.month, now.day)

    # Seed 2 labeled images today in DB
    img1 = GalleryImage(
        keyword="k1", source_image_url=_unique_url("seed1"), local_path="/tmp/fake1.jpg",
        public_url="http://p1", width=100, height=100, source_engine="bing",
        downloaded_at=now, labeled_at=now, attributes={"shot_type": "portrait"},
    )
    img2 = GalleryImage(
        keyword="k2", source_image_url=_unique_url("seed2"), local_path="/tmp/fake2.jpg",
        public_url="http://p2", width=100, height=100, source_engine="bing",
        downloaded_at=now, labeled_at=now, attributes={"shot_type": "portrait"},
    )
    db_session.add_all([img1, img2])
    db_session.commit()

    # Cap = 3: with 2 already labeled in DB, only 1 reserve should succeed
    assert reserve(cap=3, db=db_session, redis_client=r) is True
    assert reserve(cap=3, db=db_session, redis_client=r) is False


def test_force_bypasses_cap(db_session, tmp_path):
    """force=True keeps bypassing the cap."""
    r = FakeBudgetRedis()
    # Fill budget to cap in redis
    today_key = _get_today_key()
    r.set(today_key, 100)

    s = db_session.query(Settings).filter_by(id=1).first()
    if not s:
        s = Settings(id=1)
        db_session.add(s)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 2
    s.gallery_labeling_since = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "force_test.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="force", source_image_url=_unique_url("force"), local_path=str(f),
        public_url="http://p", width=100, height=100, source_engine="manual",
        downloaded_at=datetime.now(timezone.utc), attributes=None,
    )
    db_session.add(img)
    db_session.commit()

    with patch("app.services.gallery_label_budget.get_budget_redis_client", return_value=r):
        with patch("app.tasks.gallery_labels.label_image", return_value={"shot_type": "portrait"}) as mock_label:
            label_gallery_image(img.id, force=True)
            mock_label.assert_called_once()

    db_session.refresh(img)
    assert img.labeled_at is not None


def test_redis_down_no_paid_call(db_session, tmp_path):
    """Redis down -> fail closed, no paid call."""
    s = db_session.query(Settings).filter_by(id=1).first()
    if not s:
        s = Settings(id=1)
        db_session.add(s)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 10
    s.gallery_labeling_since = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "redis_down.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="down", source_image_url=_unique_url("down"), local_path=str(f),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=datetime.now(timezone.utc) - timedelta(minutes=20), attributes=None,
    )
    db_session.add(img)
    db_session.commit()

    # Broken Redis raising ConnectionError
    broken_redis = MagicMock()
    broken_redis.get.side_effect = ConnectionError("Redis unreachable")
    broken_redis.incr.side_effect = ConnectionError("Redis unreachable")

    with patch("app.services.gallery_label_budget.get_budget_redis_client", return_value=broken_redis):
        with patch("app.tasks.gallery_labels.label_image") as mock_label:
            # 1. label_gallery_image should fail closed
            label_gallery_image(img.id)
            mock_label.assert_not_called()

            # 2. label_new_gallery_images should also fail closed
            processed = label_new_gallery_images()
            assert processed == 0
            mock_label.assert_not_called()


def test_labeler_unavailable_releases_reservation(db_session, tmp_path):
    """When labeler raises LabelerUnavailable, no paid call happened -> release reserved slot."""
    r = FakeBudgetRedis()
    s = db_session.query(Settings).filter_by(id=1).first()
    if not s:
        s = Settings(id=1)
        db_session.add(s)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 1
    s.gallery_labeling_since = datetime.now(timezone.utc) - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "unavail.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="unavail", source_image_url=_unique_url("unavail"), local_path=str(f),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=datetime.now(timezone.utc) - timedelta(minutes=20), attributes=None,
    )
    db_session.add(img)
    db_session.commit()

    with patch("app.services.gallery_label_budget.get_budget_redis_client", return_value=r):
        with patch("app.tasks.gallery_labels.label_image", side_effect=LabelerUnavailable("service 503")):
            label_gallery_image(img.id)

        # Slot must have been released back; reserve consumes it again
        assert reserve(cap=1, redis_client=r) is True
        # Release the manually reserved slot so sweep has capacity
        release(redis_client=r)

        # Sweep releases too
        with patch("app.services.gallery_label_budget.get_budget_redis_client", return_value=r):
            with patch("app.tasks.gallery_labels.label_image", side_effect=LabelerUnavailable("service 503")):
                label_new_gallery_images()

        assert reserve(cap=1, redis_client=r) is True
