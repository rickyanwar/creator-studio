import pytest
from app.services.visual_engine import generate, GenerationRequest, VisualEngineError
from unittest.mock import patch, MagicMock

def test_visual_engine_sanitize_error():
    req = GenerationRequest(layout="solo", refs=("foo",), prompt="bar")
    
    class MockConfig:
        base_url = "http://test"
        api_key = "secret_123"
        provider = "test"
        
    class MockResponse:
        def __init__(self, text, status_code):
            self.text = text
            self.status_code = status_code
            self.is_success = False
        def read(self): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        
    with patch("app.services.visual_engine.get_nine_router_config", return_value=MockConfig()), \
         patch("app.services.visual_engine._image_to_b64", return_value="b64data"), \
         patch("httpx.stream") as mock_stream:
        
        # Mock db to return 0 for usage count and 60 for daily_max
        mock_db = MagicMock()
        mock_db.query.return_value.first.return_value.visual_engine_daily_max = 60
        mock_db.query.return_value.filter.return_value.count.return_value = 0
        
        # Test API key replace
        mock_stream.return_value = MockResponse('{"error": "bad token secret_123"}', 401)
        with pytest.raises(VisualEngineError) as exc:
            generate(mock_db, req)
        assert "secret_123" not in str(exc.value)
        assert "***" in str(exc.value)
        
        # Test URL replace key
        mock_stream.return_value = MockResponse('{"error": "url https://a.com?key=supersecret&b=1"}', 401)
        with pytest.raises(VisualEngineError) as exc:
            generate(mock_db, req)
        assert "supersecret" not in str(exc.value)
        assert "key=***" in str(exc.value)

        # Test empty API key
        MockConfig.api_key = ""
        mock_stream.return_value = MockResponse('{"error": "url https://a.com?token=12345"}', 401)
        with pytest.raises(VisualEngineError) as exc:
            generate(mock_db, req)
        assert "12345" not in str(exc.value)
        assert "token=***" in str(exc.value)

