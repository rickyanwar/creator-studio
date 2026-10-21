import json
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)

@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"

@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"

from app.models.gallery import GalleryImage
from app.models.settings import Settings
from app.tasks.gallery_labels import (
    _is_relabel_candidate,
    _pick_label_candidates,
    label_new_gallery_images,
)
from app.services.gallery_labeler import LabelerError, LabelerUnavailable
from zoneinfo import ZoneInfo


@pytest.fixture(autouse=True)
def _night_clock_for_queue_tests():
    with patch("app.tasks.gallery_labels._get_now", return_value=datetime(2026, 10, 19, 2, 0, tzinfo=ZoneInfo("Asia/Jakarta"))):
        yield


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Settings.__table__.create(engine, checkfirst=True)
    GalleryImage.__table__.create(engine, checkfirst=True)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def _make_image(
    id: int,
    keyword: str = "verstappen",
    downloaded_at: datetime | None = None,
    labeled_at: datetime | None = None,
    attributes: dict | None = None,
    label_attempts: int = 0,
    is_deleted: bool = False,
    local_path: str = "/tmp/fake.jpg",
) -> GalleryImage:
    return GalleryImage(
        id=id,
        keyword=keyword,
        source_image_url=f"http://img-{id}.jpg",
        local_path=local_path,
        public_url=f"http://pub-{id}.jpg",
        width=100,
        height=100,
        source_engine="bing",
        downloaded_at=downloaded_at or datetime(2026, 10, 15, 12, 0),
        labeled_at=labeled_at,
        attributes=attributes,
        label_attempts=label_attempts,
        is_deleted=is_deleted,
    )


def test_queue_priority_order_and_limit(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)
    safety_cutoff = now - timedelta(minutes=15)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=100,
    )
    db_session.add(s)

    # a. NEW candidate (unlabeled, downloaded between since and safety_cutoff)
    img_new = _make_image(1, downloaded_at=datetime(2026, 10, 15, 10, 0))

    # b. RELABEL candidate (labeled, attributes has keyword person, no keyword_person_visible key)
    img_relabel = _make_image(
        2,
        downloaded_at=datetime(2026, 10, 5, 10, 0),
        labeled_at=datetime(2026, 10, 10, 10, 0),
        attributes={"people": [{"name": "Max Verstappen", "source": "keyword"}]},
    )

    # c. BACKFILL candidate (unlabeled, downloaded_at < since)
    img_backfill = _make_image(3, downloaded_at=datetime(2026, 10, 1, 10, 0))

    db_session.add_all([img_new, img_relabel, img_backfill])
    db_session.commit()

    candidates = _pick_label_candidates(db_session, s, limit=3, safety_cutoff=safety_cutoff)
    assert len(candidates) == 3
    assert [c[0].id for c in candidates] == [1, 2, 3]
    assert [c[1] for c in candidates] == ["new", "relabel", "backfill"]


def test_queue_backfill_newest_first(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)
    safety_cutoff = now - timedelta(minutes=15)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=100,
    )
    db_session.add(s)

    # Older backfill
    img_old = _make_image(10, downloaded_at=datetime(2026, 9, 1, 10, 0))
    # Newer backfill
    img_newer = _make_image(11, downloaded_at=datetime(2026, 10, 5, 10, 0))

    db_session.add_all([img_old, img_newer])
    db_session.commit()

    candidates = _pick_label_candidates(db_session, s, limit=2, safety_cutoff=safety_cutoff)
    assert len(candidates) == 2
    # Newest old photo first
    assert candidates[0][0].id == 11
    assert candidates[1][0].id == 10
    assert candidates[0][1] == "backfill"
    assert candidates[1][1] == "backfill"


def test_queue_manual_people_never_relabeled(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)
    safety_cutoff = now - timedelta(minutes=15)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=100,
    )
    db_session.add(s)

    img_manual = _make_image(
        20,
        labeled_at=datetime(2026, 10, 10, 10, 0),
        attributes={
            "people": [{"name": "Max Verstappen", "source": "keyword"}],
            "manual_fields": ["people"],
        },
    )
    img_auto = _make_image(
        21,
        labeled_at=datetime(2026, 10, 10, 11, 0),
        attributes={
            "people": [{"name": "Max Verstappen", "source": "keyword"}],
        },
    )

    db_session.add_all([img_manual, img_auto])
    db_session.commit()

    candidates = _pick_label_candidates(db_session, s, limit=5, safety_cutoff=safety_cutoff)
    # img_manual must NOT be picked; img_auto should be picked
    assert [c[0].id for c in candidates] == [21]
    assert candidates[0][1] == "relabel"


def test_queue_b_row_with_keyword_person_visible_not_picked(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)
    safety_cutoff = now - timedelta(minutes=15)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=100,
    )
    db_session.add(s)

    img_already_verified = _make_image(
        30,
        labeled_at=datetime(2026, 10, 10, 10, 0),
        attributes={
            "people": [{"name": "Max Verstappen", "source": "keyword"}],
            "keyword_person_visible": "yes",
        },
    )
    img_already_no = _make_image(
        31,
        labeled_at=datetime(2026, 10, 10, 11, 0),
        attributes={
            "people": [],
            "keyword_person_visible": "no",
        },
    )
    img_already_unsure = _make_image(
        32,
        labeled_at=datetime(2026, 10, 10, 12, 0),
        attributes={
            "people": [],
            "keyword_person_visible": "unsure",
        },
    )

    db_session.add_all([img_already_verified, img_already_no, img_already_unsure])
    db_session.commit()

    candidates = _pick_label_candidates(db_session, s, limit=5, safety_cutoff=safety_cutoff)
    assert candidates == []


def test_label_new_gallery_images_end_to_end_and_logging(db_session, caplog):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=10,
    )
    db_session.add(s)

    img_new = _make_image(101, downloaded_at=now - timedelta(days=2))
    img_relabel = _make_image(
        102,
        downloaded_at=now - timedelta(days=20),
        labeled_at=now - timedelta(days=5),
        attributes={"people": [{"name": "Max Verstappen", "source": "keyword"}]},
    )
    img_backfill = _make_image(103, downloaded_at=now - timedelta(days=20))

    db_session.add_all([img_new, img_relabel, img_backfill])
    db_session.commit()

    with patch("os.path.exists", return_value=True), \
         patch("app.tasks.gallery_labels.reserve", return_value=True), \
         patch("app.tasks.gallery_labels.time.sleep"), \
         patch("app.tasks.gallery_labels.label_image") as mock_label:

        mock_label.side_effect = [
            {"keyword_person_visible": "yes", "people": [{"name": "Max Verstappen", "source": "keyword"}]},
            {"keyword_person_visible": "no", "people": []},
            {"keyword_person_visible": "unsure", "people": []},
        ]

        with caplog.at_level("INFO"):
            processed = label_new_gallery_images(db=db_session)

        assert processed == 3
        assert "label_new_gallery_images: new=1 relabel=1 backfill=1" in caplog.text

        # Verify attributes on relabeled row: old keyword people removed because keyword_person_visible was 'no'
        db_session.refresh(img_relabel)
        assert img_relabel.attributes["people"] == []
        assert img_relabel.attributes["keyword_person_visible"] == "no"


def test_label_new_gallery_images_error_handling(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=10,
    )
    db_session.add(s)

    img_err = _make_image(201, downloaded_at=now - timedelta(days=2), label_attempts=1)
    db_session.add(img_err)
    db_session.commit()

    with patch("os.path.exists", return_value=True), \
         patch("app.tasks.gallery_labels.reserve", return_value=True), \
         patch("app.tasks.gallery_labels.time.sleep"), \
         patch("app.tasks.gallery_labels.label_image", side_effect=LabelerError("bad response")):

        processed = label_new_gallery_images(db=db_session)
        assert processed == 0

    db_session.refresh(img_err)
    assert img_err.label_attempts == 2
    assert "bad response" in img_err.label_error


def test_label_new_gallery_images_unavailable_stops_tick(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=10,
    )
    db_session.add(s)

    img1 = _make_image(301, downloaded_at=now - timedelta(days=2), label_attempts=0)
    img2 = _make_image(302, downloaded_at=now - timedelta(days=2), label_attempts=0)
    db_session.add_all([img1, img2])
    db_session.commit()

    with patch("os.path.exists", return_value=True), \
         patch("app.tasks.gallery_labels.reserve", return_value=True), \
         patch("app.tasks.gallery_labels.release") as mock_release, \
         patch("app.tasks.gallery_labels.label_image", side_effect=LabelerUnavailable("service down")) as mock_label:

        processed = label_new_gallery_images(db=db_session)
        assert processed == 0
        assert mock_label.call_count == 1
        mock_release.assert_called_once()

    db_session.refresh(img1)
    # Unavailable does not increment attempts
    assert img1.label_attempts == 0


def test_label_new_gallery_images_daily_cap_stops_tick(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=10,
    )
    db_session.add(s)

    img = _make_image(401, downloaded_at=now - timedelta(days=2))
    db_session.add(img)
    db_session.commit()

    with patch("os.path.exists", return_value=True), \
         patch("app.tasks.gallery_labels.reserve", return_value=False), \
         patch("app.tasks.gallery_labels.label_image") as mock_label:

        processed = label_new_gallery_images(db=db_session)
        assert processed == 0
        mock_label.assert_not_called()


def test_queue_relabel_sql_prefilter_bypasses_older_non_candidates(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)
    safety_cutoff = now - timedelta(minutes=15)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=100,
    )
    db_session.add(s)

    # 900 labeled non-candidate rows with older labeled_at
    # Some without "keyword" in people, some with keyword_person_visible
    non_candidates = []
    old_time = now - timedelta(days=60)
    for i in range(1, 901):
        if i % 2 == 0:
            attrs = {"people": [{"name": "Hamilton", "source": "vision"}]}
        else:
            attrs = {"people": [{"name": "Max", "source": "keyword"}], "keyword_person_visible": "yes"}
        non_candidates.append(
            _make_image(
                1000 + i,
                labeled_at=old_time + timedelta(seconds=i),
                attributes=attrs,
            )
        )
    db_session.add_all(non_candidates)

    # 2 candidate rows with newer labeled_at
    cand1 = _make_image(
        2001,
        labeled_at=now - timedelta(days=2),
        attributes={"people": [{"name": "Max Verstappen", "source": "keyword"}]},
    )
    cand2 = _make_image(
        2002,
        labeled_at=now - timedelta(days=1),
        attributes={"people": [{"name": "Charles Leclerc", "source": "keyword"}]},
    )
    db_session.add_all([cand1, cand2])
    db_session.commit()

    candidates = _pick_label_candidates(db_session, s, limit=3, safety_cutoff=safety_cutoff)
    assert len(candidates) == 2
    assert [c[0].id for c in candidates] == [2001, 2002]
    assert all(c[1] == "relabel" for c in candidates)


def test_missing_file_sets_attempts_and_skips_next_tick(db_session):
    now = datetime(2026, 10, 19, 12, 0)
    since = datetime(2026, 10, 11, 18, 33)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=10,
    )
    db_session.add(s)

    # Missing backfill photo
    img_missing = _make_image(
        3001,
        downloaded_at=now - timedelta(days=30),
        local_path="/tmp/nonexistent_file_g3b.jpg",
    )
    # Valid backfill photo
    img_valid = _make_image(
        3002,
        downloaded_at=now - timedelta(days=31),
        local_path="/tmp/valid_file_g3b.jpg",
    )
    db_session.add_all([img_missing, img_valid])
    db_session.commit()

    def fake_exists(path):
        return path == "/tmp/valid_file_g3b.jpg"

    with patch("os.path.exists", side_effect=fake_exists), \
         patch("app.tasks.gallery_labels.reserve", return_value=True) as mock_reserve, \
         patch("app.tasks.gallery_labels.time.sleep"), \
         patch("app.tasks.gallery_labels.label_image", return_value={"keyword_person_visible": "yes", "people": []}):

        # Tick 1: picks img_missing first (newest backfill: 30 days ago vs 31 days ago)
        processed = label_new_gallery_images(db=db_session)
        assert processed == 1

        db_session.refresh(img_missing)
        assert img_missing.label_attempts == 3
        assert img_missing.label_error == "file missing"

        db_session.refresh(img_valid)
        assert img_valid.labeled_at is not None

        # Tick 2: img_missing is not picked because label_attempts == 3
        processed_tick2 = label_new_gallery_images(db=db_session)
        assert processed_tick2 == 0


def test_label_new_gallery_images_daytime_skips_old_labels_recent_and_night_runs_backfill(db_session):
    now_utc = datetime(2026, 10, 21, 7, 0, tzinfo=timezone.utc)  # 14:00 WIB (+7)
    since = datetime(2026, 10, 1, 0, 0)

    s = Settings(
        id=1,
        gallery_labeling_enabled=True,
        gallery_labeling_since=since,
        gallery_labeling_daily_cap=10,
    )
    db_session.add(s)

    # 1. Old unlabeled image (downloaded 2 days ago)
    img_old = _make_image(501, downloaded_at=now_utc.replace(tzinfo=None) - timedelta(days=2))
    # 2. 2-hour-old unlabeled image (downloaded 2 hours ago)
    img_2h = _make_image(502, downloaded_at=now_utc.replace(tzinfo=None) - timedelta(hours=2))

    db_session.add_all([img_old, img_2h])
    db_session.commit()

    with patch("os.path.exists", return_value=True), \
         patch("app.tasks.gallery_labels.reserve", return_value=True), \
         patch("app.tasks.gallery_labels.time.sleep"), \
         patch("app.tasks.gallery_labels.label_image", return_value={"keyword_person_visible": "yes", "people": []}):

        # Run at 14:00 WIB: skips img_old, but labels img_2h
        with patch("app.tasks.gallery_labels._get_now", return_value=now_utc):
            processed_day = label_new_gallery_images(db=db_session)
        assert processed_day == 1

        db_session.refresh(img_2h)
        assert img_2h.labeled_at is not None

        db_session.refresh(img_old)
        assert img_old.labeled_at is None  # Skipped during daytime!

        # Run at 02:00 WIB: night window (00:00-06:00 WIB)
        night_utc = datetime(2026, 10, 21, 19, 0, tzinfo=timezone.utc)  # 02:00 WIB next day (+7)
        with patch("app.tasks.gallery_labels._get_now", return_value=night_utc):
            processed_night = label_new_gallery_images(db=db_session)
        assert processed_night == 1

        db_session.refresh(img_old)
        assert img_old.labeled_at is not None  # Processed during night sweep!


