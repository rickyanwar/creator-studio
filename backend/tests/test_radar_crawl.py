import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.tasks.radar_crawl import radar_crawl_tick, _process_radar_tick
from app.services.ig_viewer_scraper import ViewerBusyError

@pytest.fixture
def mock_db():
    return MagicMock()

@pytest.fixture
def mock_redis():
    r = MagicMock()
    r.set.return_value = True # tick lock acquired
    r.get.return_value = None # no WAVE_KEY
    return r

@patch("app.tasks.radar_crawl._process_radar_tick")
def test_radar_crawl_tick_wave_active(mock_process, mock_db, mock_redis):
    # WAVE_KEY is active
    mock_redis.get.return_value = "1"
    
    with patch("app.tasks.radar_crawl.SessionLocal", return_value=mock_db), \
         patch("app.tasks.radar_crawl._redis", return_value=mock_redis):
        radar_crawl_tick()
        
        mock_process.assert_not_called()
        mock_redis.eval.assert_called()

@patch("app.tasks.radar_crawl._process_radar_tick")
def test_radar_crawl_tick_sleep_window(mock_process, mock_db, mock_redis):
    db_settings = MagicMock(radar_very_hot_interval_min=None, radar_hot_window_h=None, radar_hot_interval_min=None, radar_cold_interval_min=None)
    db_settings.radar_sleep_start_wib = 1
    db_settings.radar_sleep_end_wib = 6
    mock_db.query.return_value.filter_by.return_value.first.return_value = db_settings
    
    with patch("app.tasks.radar_crawl.SessionLocal", return_value=mock_db), \
         patch("app.tasks.radar_crawl._redis", return_value=mock_redis), \
         patch("app.tasks.radar_crawl.datetime") as mock_dt:
        mock_dt.now.return_value.hour = 3 # Inside sleep window
        radar_crawl_tick()
        
        mock_process.assert_not_called()
        mock_redis.eval.assert_called()

@patch("app.services.ig_viewer_scraper.fetch_many_recent_posts")
@patch("app.services.radar_ingest.ingest_medias")
def test_process_radar_tick_ordering_and_cap(mock_ingest, mock_fetch, mock_db, mock_redis):
    db_settings = MagicMock(radar_very_hot_interval_min=None, radar_hot_window_h=None, radar_hot_interval_min=None, radar_cold_interval_min=None)
    db_settings.radar_very_hot_interval_min = 8
    db_settings.radar_hot_window_h = 24
    db_settings.radar_hot_interval_min = 12
    db_settings.radar_cold_interval_min = 60
    
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    acc1 = MagicMock(ig_username="hot_acc", is_active=True, leader_score=0)
    acc2 = MagicMock(ig_username="very_hot_acc", is_active=True, leader_score=0)
    acc3 = MagicMock(ig_username="cold_leader", is_active=True, leader_score=1.0) # cold interval 30
    acc4 = MagicMock(ig_username="cold_acc", is_active=True, leader_score=0) # cold interval 60
    acc5 = MagicMock(ig_username="hot_acc2", is_active=True, leader_score=0)
    acc6 = MagicMock(ig_username="cold_acc2", is_active=True, leader_score=0)
    
    # Return 6 active radar accounts
    mock_db.query.return_value.filter.return_value.all.side_effect = [
        [acc1, acc2, acc3, acc4, acc5, acc6], # radar accounts
        [] # ig sources
    ]
    
    # Mock last checked times in redis (all long ago)
    def hget_mock(name, key):
        return (now - timedelta(minutes=100)).isoformat()
    mock_redis.hget.side_effect = hget_mock
    
    # Mock latest posts for hotness
    # 1. hot_acc -> 12 hours old -> hot (hotness=1)
    # 2. very_hot_acc -> 30 min old -> very hot (hotness=2)
    # 3. cold_leader -> 3 days old -> cold (hotness=0)
    # 4. cold_acc -> no post -> cold
    # 5. hot_acc2 -> 10 hours old -> hot
    # 6. cold_acc2 -> no post -> cold
    
    post1 = MagicMock(taken_at=now - timedelta(hours=12))
    post2 = MagicMock(taken_at=now - timedelta(minutes=30))
    post3 = MagicMock(taken_at=now - timedelta(days=3))
    post5 = MagicMock(taken_at=now - timedelta(hours=10))
    
    # Order of querying matches the order in tracked dict
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.side_effect = [
        post1, post2, post3, None, post5, None
    ]
    
    mock_fetch.return_value = {
        "very_hot_acc": [MagicMock()],
        "hot_acc": [MagicMock()],
        "hot_acc2": [MagicMock()],
        "cold_leader": [MagicMock()],
        "cold_acc": [MagicMock()]
        # cold_acc2 should be dropped because of cap 5
    }
    
    _process_radar_tick(mock_db, mock_redis, db_settings)
    
    # Verify fetch was called with at most 5 usernames
    args, kwargs = mock_fetch.call_args
    assert len(args[0]) == 5
    assert args[0][0] == "very_hot_acc" # hotness 2
    # The next should be hot_acc and hot_acc2 (hotness 1)
    # Then cold_leader (interval 30 -> more overdue than cold_acc with interval 60)
    # Then cold_acc
    assert args[0][1:3] == ["hot_acc", "hot_acc2"] or args[0][1:3] == ["hot_acc2", "hot_acc"]
    assert "cold_leader" in args[0][3:]
    assert "cold_acc2" not in args[0]
    
    assert mock_ingest.call_count == 5

@patch("app.services.ig_viewer_scraper.fetch_many_recent_posts")
def test_process_radar_tick_viewer_busy(mock_fetch, mock_db, mock_redis):
    db_settings = MagicMock(radar_very_hot_interval_min=None, radar_hot_window_h=None, radar_hot_interval_min=None, radar_cold_interval_min=None)
    
    acc1 = MagicMock(ig_username="hot_acc", is_active=True, leader_score=0)
    mock_db.query.return_value.filter.return_value.all.side_effect = [[acc1], []]
    mock_redis.hget.return_value = None
    
    # No latest_post for simplicity
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    
    mock_fetch.side_effect = ViewerBusyError("busy")
    
    _process_radar_tick(mock_db, mock_redis, db_settings)
    
    # If viewer is busy, we just return, last_checked shouldn't be updated
    mock_redis.hset.assert_not_called()

@patch("app.services.ig_viewer_scraper.fetch_many_recent_posts")
@patch("app.services.radar_ingest.ingest_medias")
def test_process_radar_tick_exception_per_account(mock_ingest, mock_fetch, mock_db, mock_redis):
    db_settings = MagicMock(radar_very_hot_interval_min=None, radar_hot_window_h=None, radar_hot_interval_min=None, radar_cold_interval_min=None)
    
    acc1 = MagicMock(ig_username="hot_acc", is_active=True, leader_score=0)
    mock_db.query.return_value.filter.return_value.all.side_effect = [[acc1], []]
    mock_redis.hget.return_value = None
    
    # No latest_post for simplicity
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    
    # fetch_many_recent_posts returns an exception as the value for the username
    mock_fetch.return_value = {"hot_acc": Exception("some error")}
    
    _process_radar_tick(mock_db, mock_redis, db_settings)
    
    # Should update last_checked anyway
    mock_redis.hset.assert_called_once()
    assert acc1.last_error == "some error"
    mock_ingest.assert_not_called()

@patch("app.services.ig_viewer_scraper.fetch_many_recent_posts")
@patch("app.tasks.crawler._ingest_mode1_medias")
@patch("app.services.radar_ingest.ingest_medias")
def test_process_radar_tick_cross_feed_mode1(mock_ingest, mock_mode1, mock_fetch, mock_db, mock_redis):
    db_settings = MagicMock(radar_very_hot_interval_min=None, radar_hot_window_h=None, radar_hot_interval_min=None, radar_cold_interval_min=None)
    
    # Has ig_source, no radar_account
    src = MagicMock(id=1, ig_username="source_acc", is_active=True)
    
    mock_db.query.return_value.filter.return_value.all.side_effect = [[], [src]]
    mock_redis.hget.return_value = None
    
    # No latest_post for simplicity
    mock_db.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
    
    # Say it is tracked by Mode 1 too
    mock_db.query.return_value.scalar.return_value = True 
    
    mock_fetch.return_value = {"source_acc": [MagicMock()]}
    
    _process_radar_tick(mock_db, mock_redis, db_settings)
    
    mock_ingest.assert_called_once()
    mock_mode1.assert_called_once()
