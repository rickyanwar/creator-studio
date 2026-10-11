import pytest
import httpx
from pathlib import Path
from app.tasks.image_saver import download_ig_image
from unittest.mock import patch, MagicMock

def test_download_ig_image_ssrf_redirect(tmp_path):
    class MockClient:
        def __init__(self, *args, **kwargs):
            self.req_url = ""
            
        def build_request(self, method, url, headers):
            self.req_url = url
            return MagicMock()
            
        def send(self, req, stream=True):
            resp = MagicMock()
            if self.req_url == "https://scontent.cdninstagram.com/first":
                resp.is_redirect = True
                resp.next_request = MagicMock(url="http://127.0.0.1/admin")
            elif self.req_url == "http://127.0.0.1/admin":
                resp.is_redirect = False
            return resp
            
        def close(self):
            pass

    with patch("httpx.Client", return_value=MockClient()):
        dest = tmp_path / "test.jpg"
        with pytest.raises(ValueError, match="URL host not allowed: http://127.0.0.1/admin"):
            download_ig_image("https://scontent.cdninstagram.com/first", dest=dest)
