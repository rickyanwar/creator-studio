import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch
from sqlalchemy.orm import Session

from app.models.radar import RadarPost
from app.models.radar import RadarSnapshot
from app.models.radar import RadarAccount
from app.services.radar_ingest import ingest_medias, IngestResult

@pytest.fixture
def db_session():
    return MagicMock(spec=Session)

@pytest.fixture
def mock_settings(db_session):
    db_settings = MagicMock()
    db_settings.radar_track_max_age_h = 48
    db_session.query.return_value.filter_by.return_value.first.return_value = db_settings
    return db_settings

def test_ingest_medias_filters_videos(db_session, mock_settings):
    now = datetime.now(timezone.utc)
    
    media = MagicMock()
    media.media_type = 2 # video
    media.taken_at = now - timedelta(hours=1)
    
    res = ingest_medias(
        db_session,
        username="test",
        radar_account_id=1,
        ig_source_id=None,
        medias=[media],
        now=now,
        thumb_fetcher=lambda u: b"image"
    )
    
    assert res.posts_inserted == 0
    assert res.posts_updated == 0
    assert res.snapshots_inserted == 0
    db_session.execute.assert_not_called()

def test_ingest_medias_filters_old_posts(db_session, mock_settings):
    now = datetime.now(timezone.utc)
    
    media = MagicMock()
    media.media_type = 1 # image
    media.taken_at = now - timedelta(hours=50) # older than 48h
    
    res = ingest_medias(
        db_session,
        username="test",
        radar_account_id=1,
        ig_source_id=None,
        medias=[media],
        now=now,
        thumb_fetcher=lambda u: b"image"
    )
    
    assert res.posts_inserted == 0
    assert res.posts_updated == 0

@patch("app.services.radar_ingest.compute_phash")
def test_ingest_medias_success(mock_compute_phash, db_session, mock_settings):
    now = datetime.now(timezone.utc)
    mock_compute_phash.return_value = "fake_phash"
    
    media = MagicMock()
    media.media_type = 1
    media.code = "shortcode123"
    media.taken_at = now - timedelta(hours=1)
    media.caption_text = "test caption"
    media.like_count = 100
    media.comment_count = 10
    media.thumbnail_url = "http://example.com/thumb.jpg"
    media.resources = []
    
    # Mocking that post doesn't exist yet
    db_session.query.return_value.filter_by.return_value.first.side_effect = [
        mock_settings, # settings
        None, # existing post
        MagicMock(last_checked_at=None, last_post_seen_at=None) # radar acc
    ]
    db_session.query.return_value.filter_by.return_value.scalar.return_value = 99 # post id
    
    # execute returns a mock that looks like an insert happened
    res_mock = MagicMock()
    res_mock.rowcount = 1
    res_mock.returned_defaults = True
    db_session.execute.return_value = res_mock
    
    res = ingest_medias(
        db_session,
        username="test",
        radar_account_id=1,
        ig_source_id=None,
        medias=[media],
        now=now,
        thumb_fetcher=lambda u: b"image"
    )
    
    assert res.posts_inserted == 1
    assert res.posts_updated == 0
    assert res.snapshots_inserted == 1
    
    db_session.execute.assert_called_once()
    db_session.add.assert_called_once() # snapshot
    db_session.commit.assert_called_once()
    
    # Check that thumb_fetcher was called
    mock_compute_phash.assert_called_once_with(b"image")

@patch("app.services.radar_ingest.compute_phash")
def test_ingest_medias_phash_failure_handled(mock_compute_phash, db_session, mock_settings):
    now = datetime.now(timezone.utc)
    mock_compute_phash.side_effect = Exception("failed to hash")
    
    media = MagicMock()
    media.media_type = 1
    media.code = "shortcode123"
    media.taken_at = now - timedelta(hours=1)
    media.thumbnail_url = "http://example.com/thumb.jpg"
    
    db_session.query.return_value.filter_by.return_value.first.side_effect = [
        mock_settings, None, MagicMock()
    ]
    db_session.query.return_value.filter_by.return_value.scalar.return_value = 99
    
    res_mock = MagicMock()
    res_mock.rowcount = 1
    res_mock.returned_defaults = True
    db_session.execute.return_value = res_mock
    
    res = ingest_medias(
        db_session,
        username="test",
        radar_account_id=1,
        ig_source_id=None,
        medias=[media],
        now=now,
        thumb_fetcher=lambda u: b"image"
    )
    
    assert res.posts_inserted == 1
    assert res.snapshots_inserted == 1

def test_ingest_medias_makes_taken_at_naive(db_session, mock_settings):
    now = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    
    media = MagicMock()
    media.media_type = 1
    media.taken_at = now - timedelta(hours=1) # aware
    media.pk = "123"
    media.code = "code"
    media.caption_text = "test"
    media.thumbnail_url = "http://example.com/thumb.jpg"
    media.like_count = 100
    media.comment_count = 10
    media.resources = []
    
    db_session.execute.return_value = MagicMock(rowcount=1)
    
    res = ingest_medias(
        db_session,
        username="user",
        radar_account_id=1,
        ig_source_id=None,
        medias=[media],
        now=now,
        thumb_fetcher=lambda u: b"thumb"
    )
    
    assert res.posts_inserted == 1 # we use execute for upsert
    # let's inspect the upsert statement
    assert db_session.execute.called
    stmt = db_session.execute.call_args[0][0]
    # compiled statement values
    params = stmt.compile().params
    
    # either param name is taken_at or the dictionary contains a naive datetime
    for k, v in params.items():
        if isinstance(v, datetime):
            assert v.tzinfo is None
