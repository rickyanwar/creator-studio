import uuid
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from app.models.gallery import GalleryImage
from app.models.settings import Settings
from app.tasks.gallery_labels import label_gallery_image, label_new_gallery_images
from app.services.gallery_labeler import LabelerUnavailable, LabelerError
from app.database import Base, engine, SessionLocal
from tests.test_gallery_label_budget import FakeBudgetRedis


@pytest.fixture(autouse=True)
def _mock_budget_redis():
    fake_r = FakeBudgetRedis()
    with patch("app.services.gallery_label_budget.get_budget_redis_client", return_value=fake_r):
        yield fake_r


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


def test_task_disabled_or_no_since(db_session, monkeypatch):
    s = db_session.query(Settings).filter_by(id=1).first()
    if not s:
        s = Settings(id=1, gallery_labeling_enabled=False, gallery_labeling_since=None)
        db_session.add(s)
    else:
        s.gallery_labeling_enabled = False
        s.gallery_labeling_since = None
    db_session.commit()

    with patch("app.tasks.gallery_labels.label_image") as mock_label:
        processed = label_new_gallery_images()
        assert processed == 0
        mock_label.assert_not_called()


def test_task_daily_cap_respected(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 2
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    img1 = GalleryImage(
        keyword="k1", source_image_url=_unique_url("img1"), local_path=str(tmp_path / "1.jpg"),
        public_url="http://p1", width=100, height=100, source_engine="bing",
        downloaded_at=now, labeled_at=now, attributes={"shot_type": "portrait"},
    )
    img2 = GalleryImage(
        keyword="k2", source_image_url=_unique_url("img2"), local_path=str(tmp_path / "2.jpg"),
        public_url="http://p2", width=100, height=100, source_engine="bing",
        downloaded_at=now, labeled_at=now, attributes={"shot_type": "portrait"},
    )
    img3_file = tmp_path / "3.jpg"
    img3_file.write_text("fake")
    img3 = GalleryImage(
        keyword="k3", source_image_url=_unique_url("img3"), local_path=str(img3_file),
        public_url="http://p3", width=100, height=100, source_engine="bing",
        downloaded_at=now, attributes=None, label_attempts=0,
    )
    db_session.add_all([img1, img2, img3])
    db_session.commit()

    with patch("app.tasks.gallery_labels.label_image") as mock_label:
        processed = label_new_gallery_images()
        assert processed == 0
        mock_label.assert_not_called()


def test_task_value_error_increments_attempts(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "fail.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="fail", source_image_url=_unique_url("fail"), local_path=str(f),
        public_url="http://pfail", width=100, height=100, source_engine="bing",
        downloaded_at=now - timedelta(minutes=20), attributes=None, label_attempts=0,
    )
    db_session.add(img)
    db_session.commit()

    with patch("app.tasks.gallery_labels.label_image", side_effect=ValueError("invalid json")):
        label_new_gallery_images()

    db_session.refresh(img)
    assert img.label_attempts == 1
    assert "invalid json" in img.label_error


def test_task_unavailable_stops_tick_without_incrementing(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "unavail.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="unavail", source_image_url=_unique_url("unavail"), local_path=str(f),
        public_url="http://punavail", width=100, height=100, source_engine="bing",
        downloaded_at=now - timedelta(minutes=20), attributes=None, label_attempts=0,
    )
    db_session.add(img)
    db_session.commit()

    with patch("app.tasks.gallery_labels.label_image", side_effect=LabelerUnavailable("429 rate limit")):
        processed = label_new_gallery_images()

    db_session.refresh(img)
    assert img.label_attempts == 0
    assert img.attributes is None


def test_task_skips_attempts_ge_3_and_deleted(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    f1 = tmp_path / "3att.jpg"
    f1.write_text("fake")
    img1 = GalleryImage(
        keyword="k", source_image_url=_unique_url("3att"), local_path=str(f1),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now, attributes=None, label_attempts=3,
    )
    f2 = tmp_path / "del.jpg"
    f2.write_text("fake")
    img2 = GalleryImage(
        keyword="k", source_image_url=_unique_url("del"), local_path=str(f2),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now, attributes=None, label_attempts=0, is_deleted=True,
    )
    # Missing file
    img3 = GalleryImage(
        keyword="k", source_image_url=_unique_url("nofile"), local_path=str(tmp_path / "missing.jpg"),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now, attributes=None, label_attempts=0,
    )
    db_session.add_all([img1, img2, img3])
    db_session.commit()

    with patch("app.tasks.gallery_labels.label_image") as mock_label:
        processed = label_new_gallery_images()
        assert processed == 0
        mock_label.assert_not_called()


def test_downloader_enqueues_when_enabled(tmp_path):
    with patch("app.tasks.gallery_labels.label_gallery_image.delay") as mock_delay:
        from app.tasks.gallery_downloader import _enqueue_label_if_enabled

        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.gallery_labeling_enabled = True
        mock_db.query().filter_by().first.return_value = mock_settings

        _enqueue_label_if_enabled(mock_db, 123)
        mock_delay.assert_called_once_with(123)

        mock_delay.reset_mock()
        mock_settings.gallery_labeling_enabled = False
        _enqueue_label_if_enabled(mock_db, 123)
        mock_delay.assert_not_called()


def test_downloader_enqueue_swallows_broker_exception():
    with patch("app.tasks.gallery_labels.label_gallery_image.delay", side_effect=Exception("Redis connection refused")):
        from app.tasks.gallery_downloader import _enqueue_label_if_enabled

        mock_db = MagicMock()
        mock_settings = MagicMock()
        mock_settings.gallery_labeling_enabled = True
        mock_db.query().filter_by().first.return_value = mock_settings

        # Must not raise
        _enqueue_label_if_enabled(mock_db, 123)


def test_task_safety_net_15_minute_cutoff(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    # Image downloaded 5 minutes ago (< 15 min cutoff) -> should be skipped by safety net
    f_recent = tmp_path / "recent.jpg"
    f_recent.write_text("fake")
    img_recent = GalleryImage(
        keyword="recent", source_image_url=_unique_url("recent"), local_path=str(f_recent),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now - timedelta(minutes=5), attributes=None, label_attempts=0,
    )
    # Image downloaded 20 minutes ago (>= 15 min cutoff) -> should be picked
    f_old = tmp_path / "old.jpg"
    f_old.write_text("fake")
    img_old = GalleryImage(
        keyword="old", source_image_url=_unique_url("old"), local_path=str(f_old),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now - timedelta(minutes=20), attributes=None, label_attempts=0,
    )
    db_session.add_all([img_recent, img_old])
    db_session.commit()

    with patch("app.tasks.gallery_labels.label_image", return_value={"shot_type": "portrait", "people_count": 1, "activity": "talking", "expression": "neutral", "context": {"session": "unknown", "rain": False, "night": False, "circuit_visible": False}, "livery_colors": [], "quality": {"blurry": False, "obstructed": False}, "caption": ""}):
        processed = label_new_gallery_images()
        assert processed == 1

    db_session.refresh(img_recent)
    db_session.refresh(img_old)
    assert img_recent.attributes is None
    assert img_old.attributes is not None


def test_stage_one_labels_date_only_attributes_and_preserves_metadata(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    image_file = tmp_path / "date-only.jpg"
    image_file.write_text("fake")
    img = GalleryImage(
        keyword="max verstappen f1", source_image_url=_unique_url("date-only"),
        local_path=str(image_file), public_url="http://p", width=100, height=100,
        source_engine="bing", downloaded_at=now - timedelta(minutes=20),
        captured_at=datetime(2026, 1, 2).date(),
        attributes={"captured_at_source": "getty_id", "getty_hints": {"asset_id": "abc"}},
        label_attempts=0,
    )
    db_session.add(img)
    db_session.commit()

    output = {"shot_type": "portrait", "people_count": 1, "activity": "talking", "expression": "neutral", "context": {"session": "unknown", "rain": False, "night": False, "circuit_visible": False}, "livery_colors": [], "quality": {"blurry": False, "obstructed": False}, "caption": "", "people": []}
    with patch("app.tasks.gallery_labels.label_image", return_value=output):
        assert label_new_gallery_images() == 1

    db_session.refresh(img)
    assert img.labeled_at is not None
    assert img.attributes["captured_at_source"] == "getty_id"
    assert img.attributes["getty_hints"] == {"asset_id": "abc"}


def test_label_gallery_image_skips_none_downloaded_at(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "nodate.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="nodate", source_image_url=_unique_url("nodate"), local_path=str(f),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now, attributes=None, label_attempts=0,
    )
    db_session.add(img)
    db_session.commit()

    # Manually simulate a row or instance where downloaded_at is None (in-memory or legacy)
    with patch("app.tasks.gallery_labels.SessionLocal") as mock_session_factory:
        mock_db = MagicMock()
        mock_session_factory.return_value = mock_db
        mock_db.query(Settings).filter_by().first.return_value = s
        mock_img = MagicMock()
        mock_img.id = img.id
        mock_img.is_deleted = False
        mock_img.attributes = None
        mock_img.downloaded_at = None
        mock_db.query(GalleryImage).filter_by().first.return_value = mock_img

        with patch("app.tasks.gallery_labels._get_daily_budget", return_value=10):
            with patch("app.tasks.gallery_labels.label_image") as mock_label:
                label_gallery_image(img.id)
                mock_label.assert_not_called()


def test_label_gallery_image_force_bypasses_settings(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    s.gallery_labeling_enabled = False
    s.gallery_labeling_since = None
    db_session.commit()
    image_file = tmp_path / "force.jpg"
    image_file.write_text("fake")
    img = GalleryImage(
        keyword="forced", source_image_url=_unique_url("forced"), local_path=str(image_file),
        public_url="http://p", width=100, height=100, source_engine="manual",
        downloaded_at=datetime.now(timezone.utc), attributes=None,
    )
    db_session.add(img)
    db_session.commit()
    with patch("app.tasks.gallery_labels.label_image", return_value={"shot_type": "portrait"}):
        label_gallery_image(img.id, force=True)
    db_session.refresh(img)
    assert img.labeled_at is not None


def test_label_gallery_image_passes_owner_hints_when_relabeling(db_session, tmp_path):
    s = db_session.query(Settings).filter_by(id=1).first()
    s.gallery_labeling_enabled = False
    s.gallery_labeling_since = None
    db_session.commit()
    image_file = tmp_path / "relabel.jpg"
    image_file.write_text("fake")
    img = GalleryImage(
        keyword="forced", source_image_url=_unique_url("relabel"), local_path=str(image_file),
        public_url="http://p", width=100, height=100, source_engine="manual",
        downloaded_at=datetime.now(timezone.utc), labeled_at=datetime.now(timezone.utc),
        attributes={"owner_hints": {"session": "race"}, "manual_fields": ["event.session"], "event": {"session": "race"}},
    )
    db_session.add(img)
    db_session.commit()
    with patch("app.tasks.gallery_labels.label_image", return_value={"shot_type": "portrait"}) as mock_label:
        label_gallery_image(img.id, force=True, relabel=True)
    assert mock_label.call_args.kwargs["hints"] == {"session": "race"}



def test_task_ends_the_transaction_before_each_vision_call(db_session, tmp_path):
    # Incident 2026-10-24: an open transaction during a slow vision call blocked a migration.
    s = db_session.query(Settings).filter_by(id=1).first()
    now = datetime.now(timezone.utc)
    s.gallery_labeling_enabled = True
    s.gallery_labeling_daily_cap = 100
    s.gallery_labeling_since = now - timedelta(days=1)
    db_session.commit()

    f = tmp_path / "tx.jpg"
    f.write_text("fake")
    img = GalleryImage(
        keyword="tx-check", source_image_url=_unique_url("tx"), local_path=str(f),
        public_url="http://p", width=100, height=100, source_engine="bing",
        downloaded_at=now - timedelta(minutes=20), attributes=None, label_attempts=0,
    )
    db_session.add(img)
    db_session.commit()

    seen = {}

    def fake_label(path, **kwargs):
        seen[path] = (kwargs.get("keyword"), db_session.in_transaction())
        return {"shot_type": "portrait", "people_count": 1, "activity": "talking", "expression": "neutral", "context": {"session": "unknown", "rain": False, "night": False, "circuit_visible": False}, "livery_colors": [], "quality": {"blurry": False, "obstructed": False}, "caption": ""}

    with patch("app.tasks.gallery_labels.label_image", side_effect=fake_label):
        label_new_gallery_images(db=db_session)

    assert seen[str(f)] == ("tx-check", False)
