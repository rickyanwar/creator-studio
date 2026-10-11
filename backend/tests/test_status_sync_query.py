import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy.dialects import postgresql

def test_sync_query_postgres_dialect_no_fallback():
    mock_db = MagicMock()
    mock_db.bind.dialect.name = "postgresql"
    
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_filter = MagicMock()
    mock_query.filter.return_value = mock_filter
    mock_order = MagicMock()
    mock_filter.order_by.return_value = mock_order
    mock_limit = MagicMock()
    mock_order.limit.return_value = mock_limit
    mock_limit.all.return_value = []
    
    with patch("app.tasks.status_sync.SessionLocal", return_value=mock_db):
        from app.tasks.status_sync import sync_pending_schedules
        sync_pending_schedules()
        
    mock_query.filter.assert_called_once()
    args, kwargs = mock_query.filter.call_args
    # args should contain a BinaryExpression with json_extract_path_text
    compiled_conditions = [str(arg.compile(dialect=postgresql.dialect())) for arg in args]
    
    has_json_extract = any("json_extract_path_text" in c for c in compiled_conditions)
    has_constant_true = any(c == "true" or c == "1" for c in compiled_conditions)
    
    assert has_json_extract, "Postgres dialect should use json_extract_path_text"
    assert not has_constant_true, "Postgres dialect should NOT fallback to True"

    mock_filter.order_by.assert_called_once()
    order_args, _ = mock_filter.order_by.call_args
    compiled_order = str(order_args[0].compile(dialect=postgresql.dialect()))
    assert "DESC" in compiled_order, "Should order by newest first"

def test_sync_query_sqlite_fallback():
    mock_db = MagicMock()
    mock_db.bind.dialect.name = "sqlite"
    
    mock_query = MagicMock()
    mock_db.query.return_value = mock_query
    mock_filter = MagicMock()
    mock_query.filter.return_value = mock_filter
    mock_order = MagicMock()
    mock_filter.order_by.return_value = mock_order
    mock_limit = MagicMock()
    mock_order.limit.return_value = mock_limit
    mock_limit.all.return_value = []
    
    with patch("app.tasks.status_sync.SessionLocal", return_value=mock_db):
        from app.tasks.status_sync import sync_pending_schedules
        sync_pending_schedules()
        
    args, kwargs = mock_query.filter.call_args
    assert True in args, "SQLite dialect should fallback to True"
