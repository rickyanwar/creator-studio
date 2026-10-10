import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.scripts.visual_engine_health import get_health, main
from app.models.ai_copy_events import AICopyEvent

@pytest.fixture
def mock_db():
    return MagicMock()

def make_event(id, created_at, outcome, error_message=None):
    ev = AICopyEvent(
        id=id,
        created_at=created_at,
        context="image_gen",
        outcome=outcome,
        error_message=error_message
    )
    return ev

def test_get_health_healthy(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [
        make_event(1, now_naive - timedelta(minutes=10), "success"),
        make_event(2, now_naive - timedelta(minutes=20), "recovered"),
        make_event(3, now_naive - timedelta(minutes=30), "success"),
    ]
    
    mock_query = mock_db.query.return_value
    mock_filter = mock_query.filter.return_value
    mock_order = mock_filter.order_by.return_value
    mock_order.limit.return_value.all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 3
    assert health["failed"] == 0
    assert health["fallback"] == 1
    assert health["consecutive_failures"] == 0
    assert not health["unhealthy"]

def test_get_health_unhealthy_consecutive(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [make_event(i, now_naive - timedelta(minutes=i), "failed", f"err {i}") for i in range(1, 6)]
    events.append(make_event(6, now_naive - timedelta(minutes=6), "success"))
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["consecutive_failures"] == 5
    assert health["unhealthy"]
    assert health["last_error"] == "err 1"

def test_get_health_unhealthy_fallback_rate(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [make_event(i, now_naive - timedelta(minutes=i), "recovered") for i in range(1, 11)]
    events.extend([make_event(i, now_naive - timedelta(minutes=i), "success") for i in range(11, 21)])
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 20
    assert health["fallback"] == 10
    assert health["unhealthy"]

def test_get_health_healthy_fallback_rate_under_20_total(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [make_event(i, now_naive - timedelta(minutes=i), "recovered") for i in range(1, 11)]
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 10
    assert health["fallback"] == 10
    assert not health["unhealthy"] # Must be >= 20 total for fallback threshold

def test_get_health_ignores_old_events(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [
        make_event(1, now_naive - timedelta(hours=1), "failed", "err"),
        make_event(2, now_naive - timedelta(hours=7), "failed", "err"), # older than 6h
    ]
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 1
    assert health["consecutive_failures"] == 1
    assert not health["unhealthy"]

@patch("app.scripts.visual_engine_health.SessionLocal")
@patch("app.scripts.visual_engine_health.get_health")
def test_main_success(mock_get_health, mock_session_local, capsys):
    mock_get_health.return_value = {"status": "ok"}
    assert main([]) == 0
    out, _ = capsys.readouterr()
    assert '"status": "ok"' in out

@patch("app.scripts.visual_engine_health.SessionLocal")
def test_main_exception(mock_session_local, capsys):
    mock_session_local.side_effect = Exception("DB error")
    assert main([]) == 2
    out, _ = capsys.readouterr()
    assert '"error": "health_unavailable"' in out
