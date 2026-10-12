import os
import hashlib
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import pytest
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings


@pytest.fixture
def thumb_storage(tmp_path, monkeypatch):
    storage_root = tmp_path / "media"
    storage_root.mkdir()
    (storage_root / "designs").mkdir()
    (storage_root / "posts").mkdir()
    (storage_root / "videos").mkdir()
    (storage_root / "gallery").mkdir()

    s = get_settings()
    monkeypatch.setattr(s, "storage_base_path", str(storage_root))
    monkeypatch.setattr(s, "storage_base_url", "http://localhost/media")
    return storage_root


@pytest.fixture
def client():
    return TestClient(app)


def create_test_image(path: Path, width=800, height=600, color="red", fmt="JPEG"):
    path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.new("RGB", (width, height), color=color)
    img.save(str(path), format=fmt)
    return path


def test_generate_and_cache_thumbnail(client, thumb_storage):
    img_path = thumb_storage / "designs" / "test1.png"
    create_test_image(img_path, width=1080, height=1350, fmt="PNG")

    # Call with media url
    url = "http://localhost/media/designs/test1.png"
    resp = client.get(f"/media/thumb?u={url}&w=360")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"
    assert resp.headers["cache-control"] == "public, max-age=604800"

    # Verify cached file on disk
    rel_path = "designs/test1.png"
    mtime = str(os.path.getmtime(str(img_path)))
    cache_key = hashlib.sha1(f"{rel_path}:{mtime}".encode("utf-8")).hexdigest()
    cached_file = thumb_storage / "thumbs" / "360" / f"{cache_key}.webp"
    assert cached_file.exists()

    # Verify image dimensions
    with Image.open(cached_file) as im:
        assert im.format == "WEBP"
        assert im.width == 360
        # aspect ratio 1080x1350 -> 360x450
        assert im.height == 450

    # Second call uses cache without re-encoding
    with patch("PIL.Image.open") as mock_open:
        resp2 = client.get(f"/media/thumb?u={url}&w=360")
        assert resp2.status_code == 200
        mock_open.assert_not_called()


def test_relative_or_path_url(client, thumb_storage):
    img_path = thumb_storage / "videos" / "thumb.jpg"
    create_test_image(img_path, width=720, height=480, fmt="JPEG")

    # Test path form /media/videos/thumb.jpg
    resp = client.get("/media/thumb?u=/media/videos/thumb.jpg&w=240")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/webp"

    # Test relative form videos/thumb.jpg
    resp2 = client.get("/media/thumb?u=videos/thumb.jpg&w=480")
    assert resp2.status_code == 200
    assert resp2.headers["content-type"] == "image/webp"


def test_path_traversal_guards(client, thumb_storage, tmp_path):
    # Attempt 1: ../../etc/passwd
    resp1 = client.get("/media/thumb?u=../../etc/passwd&w=360")
    assert resp1.status_code == 404

    # Attempt 2: Absolute path outside storage root
    outside_file = tmp_path / "secret.png"
    create_test_image(outside_file)
    resp2 = client.get(f"/media/thumb?u={outside_file}&w=360")
    assert resp2.status_code == 404

    # Attempt 3: Symlink pointing outside storage root
    symlink_path = thumb_storage / "designs" / "evil_symlink.png"
    symlink_path.symlink_to(outside_file)
    resp3 = client.get("/media/thumb?u=designs/evil_symlink.png&w=360")
    assert resp3.status_code == 404


def test_non_image_extension_returns_404(client, thumb_storage):
    bad_file = thumb_storage / "designs" / "notes.txt"
    bad_file.write_text("hello")
    resp = client.get("/media/thumb?u=designs/notes.txt&w=360")
    assert resp.status_code == 404


def test_missing_source_returns_404(client, thumb_storage):
    resp = client.get("/media/thumb?u=designs/non_existent.jpg&w=360")
    assert resp.status_code == 404


def test_bad_width_returns_422(client, thumb_storage):
    img_path = thumb_storage / "designs" / "test.png"
    create_test_image(img_path)
    resp = client.get("/media/thumb?u=designs/test.png&w=500")
    # Bad w limited to {240, 360, 480} returns 422
    assert resp.status_code == 422


def test_decompression_bomb_returns_404(client, thumb_storage):
    img_path = thumb_storage / "designs" / "bomb.png"
    create_test_image(img_path)

    from PIL import Image as PILImage
    with patch("PIL.Image.open", side_effect=PILImage.DecompressionBombError("bomb")):
        resp = client.get("/media/thumb?u=designs/bomb.png&w=360")
        assert resp.status_code == 404
