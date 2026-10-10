import pytest
from app.tasks.image_saver import _validate_ig_url

def test_validate_ig_url():
    # Allowed
    assert _validate_ig_url("https://scontent.cdninstagram.com/v/t51.2885-15/e35/123.jpg") is True
    assert _validate_ig_url("https://scontent-iad3-1.cdninstagram.com/v/t51.2885-15/e35/123.jpg") is True
    assert _validate_ig_url("https://instagram.fmaa1-1.fna.fbcdn.net/v/t51.2885-15/123.jpg") is True

    # Blocked (http, IP, local, wrong domain)
    assert _validate_ig_url("http://scontent.cdninstagram.com/123.jpg") is False
    assert _validate_ig_url("https://169.254.169.254/latest/meta-data/") is False
    assert _validate_ig_url("http://localhost/image.jpg") is False
    assert _validate_ig_url("https://evil.com/123.jpg") is False
    assert _validate_ig_url("https://evil.com?q=https://scontent.cdninstagram.com/123.jpg") is False
    assert _validate_ig_url("https://cdninstagram.com.evil.com/123.jpg") is False
    assert _validate_ig_url("https://scontent.cdninstagram.com.evil.net/123.jpg") is False
