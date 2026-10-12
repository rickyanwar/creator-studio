import os
import time
from pathlib import Path
from app.tasks.celery_app import celery_app
from app.tasks.cleanup import cleanup_old_thumbs
from app.config import get_settings


def test_celery_include_and_beat():
    # Make sure app.tasks.cleanup is in celery_app.conf.include
    assert "app.tasks.cleanup" in celery_app.conf.include

    # Check beat schedule includes weekly cleanup_old_thumbs
    beat = celery_app.conf.beat_schedule
    found = any(item.get("task") == "app.tasks.cleanup.cleanup_old_thumbs" for item in beat.values())
    assert found, "cleanup_old_thumbs task not registered in celery beat schedule"


def test_cleanup_old_thumbs_deletes_files_older_than_30_days(tmp_path, monkeypatch):
    s = get_settings()
    storage_root = tmp_path / "media"
    thumbs_root = storage_root / "thumbs" / "360"
    thumbs_root.mkdir(parents=True)
    monkeypatch.setattr(s, "storage_base_path", str(storage_root))

    old_file = thumbs_root / "old.webp"
    old_file.write_bytes(b"old")
    new_file = thumbs_root / "new.webp"
    new_file.write_bytes(b"new")

    # Set mtime on old_file to 32 days ago
    past_mtime = time.time() - (32 * 86400)
    os.utime(str(old_file), (past_mtime, past_mtime))

    cleaned = cleanup_old_thumbs()
    assert cleaned == 1
    assert not old_file.exists()
    assert new_file.exists()
