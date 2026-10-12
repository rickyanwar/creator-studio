"""Tests for crawler viewer/backend routing (S1a).

No network, no real DB/Redis.
Stubs app.services.ig_viewer_scraper if the module is absent.
pytest -q backend/tests/test_crawler_viewer_routing.py
"""

from unittest.mock import MagicMock, patch
import pytest

import app.services.ig_viewer_scraper as _viewer_mod
from app.services.ig_viewer_scraper import ViewerBusyError, ViewerScrapeError


# ── shared mock-builder helpers ───────────────────────────────────────────────

def _make_db(global_scraper_mode="auto"):
    """Return a MagicMock db whose Settings row has the given scraper_mode."""
    db = MagicMock()
    settings_row = MagicMock()
    settings_row.scraper_mode = global_scraper_mode

    # db.query(DBSettings).filter_by(id=1).first() → settings_row
    db.query.return_value.filter_by.return_value.first.return_value = settings_row
    return db


def _make_source(username="testuser", scraper_backend=None):
    src = MagicMock()
    src.ig_username = username
    src.scraper_backend = scraper_backend
    src.last_crawl_error = "old error"
    return src


# ── import the functions under test ──────────────────────────────────────────

# Must happen AFTER stub is installed and AFTER we stub out heavy dependencies
# that would be imported at module level.  crawler.py uses lazy imports inside
# functions so direct function imports are fine.

from app.tasks.crawler import _resolve_effective_backend, _fetch_medias


# ── ScraperBackend enum from the real model ───────────────────────────────────

from app.models.ig_sources import ScraperBackend


# ═════════════════════════════════════════════════════════════════════════════
# 1. _resolve_effective_backend
# ═════════════════════════════════════════════════════════════════════════════

class TestResolveEffectiveBackend:

    def test_global_viewer_returns_viewer(self):
        db = _make_db(global_scraper_mode="viewer")
        result = _resolve_effective_backend(ScraperBackend.instagrapi, db)
        assert result == ScraperBackend.viewer

    def test_global_flashapi_returns_viewer(self):
        db = _make_db(global_scraper_mode="flashapi")
        result = _resolve_effective_backend(ScraperBackend.instagrapi, db)
        assert result == ScraperBackend.viewer

    def test_global_instagrapi_returns_viewer(self):
        db = _make_db(global_scraper_mode="instagrapi")
        result = _resolve_effective_backend(ScraperBackend.viewer, db)
        assert result == ScraperBackend.viewer

    def test_global_auto_per_source_flashapi_returns_viewer(self):
        db = _make_db(global_scraper_mode="auto")
        result = _resolve_effective_backend(ScraperBackend.flashapi, db)
        assert result == ScraperBackend.viewer

    def test_global_auto_per_source_auto_returns_viewer(self):
        db = _make_db(global_scraper_mode="auto")
        result = _resolve_effective_backend(ScraperBackend.auto, db)
        assert result == ScraperBackend.viewer

    def test_missing_settings_row_returns_viewer(self):
        """No Settings row → still resolves to viewer."""
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        result = _resolve_effective_backend(ScraperBackend.instagrapi, db)
        assert result == ScraperBackend.viewer

    def test_missing_settings_row_per_source_none_returns_viewer(self):
        """No Settings row, per-source None → viewer."""
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        result = _resolve_effective_backend(None, db)
        assert result == ScraperBackend.viewer


# ═════════════════════════════════════════════════════════════════════════════
# 2. _fetch_medias
# ═════════════════════════════════════════════════════════════════════════════

class TestFetchMediasAllBackendsUseViewer:

    def _db(self):
        db = MagicMock()
        return db

    @pytest.mark.parametrize("backend", [
        ScraperBackend.auto,
        ScraperBackend.instagrapi,
        ScraperBackend.flashapi,
        ScraperBackend.viewer,
    ])
    def test_all_backends_call_viewer_fetch_recent_posts(self, backend):
        """auto, instagrapi, flashapi, viewer all use the viewer path."""
        db = self._db()
        source = _make_source()

        fake_medias = [MagicMock(), MagicMock()]
        fetch_mock = MagicMock(return_value=fake_medias)

        with patch.object(_viewer_mod, "fetch_recent_posts", fetch_mock):
            result = _fetch_medias(source, backend, db, 10)

        fetch_mock.assert_called_once_with(source.ig_username, amount=10)
        assert result == fake_medias
        assert source.last_crawl_error is None


class TestFetchMediasViewer:

    def _db_viewer(self):
        db = MagicMock()
        settings_row = MagicMock()
        settings_row.scraper_mode = "viewer"

        from app.models.settings import Settings as DBSettings

        def _query(model):
            q = MagicMock()
            if model is DBSettings:
                q.filter_by.return_value.first.return_value = settings_row
            else:
                q.filter_by.return_value.first.return_value = None
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = _query
        return db

    def test_viewer_backend_calls_fetch_recent_posts(self):
        db = self._db_viewer()
        source = _make_source()

        fake_medias = [MagicMock()]
        fetch_mock = MagicMock(return_value=fake_medias)

        with patch.object(
            _viewer_mod,
            "fetch_recent_posts",
            fetch_mock,
        ):
            result = _fetch_medias(source, ScraperBackend.viewer, db, 12)

        fetch_mock.assert_called_once_with(source.ig_username, amount=12)
        assert result == fake_medias

    def test_viewer_scrape_error_sets_last_crawl_error_and_returns_empty(self):
        """ViewerScrapeError → [] and last_crawl_error starts with 'Viewer: all tiers failed ('."""
        db = self._db_viewer()
        source = _make_source()

        tier_errors = {"gramsnap": "timeout", "anonyig": "cf"}
        exc = ViewerScrapeError(tier_errors=tier_errors)
        fetch_mock = MagicMock(side_effect=exc)

        with patch.object(
            _viewer_mod,
            "fetch_recent_posts",
            fetch_mock,
        ):
            result = _fetch_medias(source, ScraperBackend.viewer, db, 12)

        assert result == []
        assert source.last_crawl_error is not None
        assert source.last_crawl_error.startswith("Viewer: all tiers failed (")
        assert "gramsnap: timeout" in source.last_crawl_error
        assert "anonyig: cf" in source.last_crawl_error

    def test_viewer_scrape_error_truncated_to_512(self):
        """Huge tier error strings → last_crawl_error <= 512 chars."""
        db = self._db_viewer()
        source = _make_source()

        tier_errors = {f"tier{i}": "x" * 200 for i in range(10)}
        exc = ViewerScrapeError(tier_errors=tier_errors)

        with patch.object(
            _viewer_mod,
            "fetch_recent_posts",
            MagicMock(side_effect=exc),
        ):
            _fetch_medias(source, ScraperBackend.viewer, db, 12)

        assert len(source.last_crawl_error) <= 512

    def test_viewer_busy_error_propagates(self):
        """ViewerBusyError must propagate out of _fetch_medias (not swallowed)."""
        db = self._db_viewer()
        source = _make_source()

        with patch.object(
            _viewer_mod,
            "fetch_recent_posts",
            MagicMock(side_effect=ViewerBusyError("locked")),
        ):
            with pytest.raises(ViewerBusyError):
                _fetch_medias(source, ScraperBackend.viewer, db, 12)


# ═════════════════════════════════════════════════════════════════════════════
# 3. crawl_single_source — ViewerBusyError → task.retry
# ═════════════════════════════════════════════════════════════════════════════

def _run_crawl_single_source_with_busy(source_id, source, settings_row, fixed_countdown=None):
    """Helper: run crawl_single_source.__wrapped__ with mocked DB and _fetch_medias→ViewerBusyError.

    For bind=True tasks, __wrapped__ is a bound method on the task instance.
    We patch crawl_single_source.retry directly (it IS self.retry inside the fn).
    Returns the recorded retry call kwargs.
    """
    from app.tasks.crawler import crawl_single_source
    from app.models.ig_sources import IGSource
    from app.models.settings import Settings as DBSettings

    mock_db = MagicMock()

    def _query(model):
        q = MagicMock()
        if model is IGSource:
            q.filter_by.return_value.first.return_value = source
        elif model is DBSettings:
            q.filter_by.return_value.first.return_value = settings_row
        else:
            q.filter_by.return_value.first.return_value = None
            q.filter.return_value.all.return_value = []
        return q

    mock_db.query.side_effect = _query

    recorded = {}

    def _fake_retry(**kwargs):
        recorded.update(kwargs)
        raise Exception("retry-stop")

    patches = [
        patch("app.tasks.crawler.SessionLocal", return_value=mock_db),
        patch("app.tasks.crawler._fetch_medias", side_effect=ViewerBusyError("locked")),
        patch.object(crawl_single_source, "retry", side_effect=_fake_retry),
    ]
    if fixed_countdown is not None:
        patches.append(patch("random.randint", return_value=fixed_countdown))

    with patches[0], patches[1], patches[2]:
        if fixed_countdown is not None:
            with patches[3]:
                try:
                    crawl_single_source.__wrapped__(source_id)
                except Exception:
                    pass
        else:
            try:
                crawl_single_source.__wrapped__(source_id)
            except Exception:
                pass

    return recorded, mock_db


class TestCrawlSingleSourceViewerBusy:
    """When _fetch_medias raises ViewerBusyError, crawl_single_source must call
    self.retry(countdown=120-300, max_retries=8) without writing last_crawl_error."""

    def _make_scenario(self):
        from app.models.ig_sources import ScraperBackend
        source = _make_source()
        source.id = 42
        source.is_active = True
        source.scraper_backend = ScraperBackend.viewer
        source.last_crawl_error = None

        settings_row = MagicMock()
        settings_row.scraper_mode = "viewer"
        settings_row.max_post_age_days = 7
        settings_row.crawl_interval_minutes = 30
        return source, settings_row

    def test_viewer_busy_calls_retry_correct_params(self):
        source, settings_row = self._make_scenario()
        fixed_countdown = 180

        recorded, mock_db = _run_crawl_single_source_with_busy(
            42, source, settings_row, fixed_countdown=fixed_countdown
        )

        assert recorded, "retry was never called"
        assert recorded.get("countdown") == fixed_countdown
        assert recorded.get("max_retries") == 8
        mock_db.rollback.assert_called_once_with()
        # last_crawl_error must NOT be written
        assert source.last_crawl_error is None

    def test_viewer_busy_countdown_range(self):
        """Countdown arg to retry must be in [120, 300] with real randint."""
        source, settings_row = self._make_scenario()

        recorded, _ = _run_crawl_single_source_with_busy(42, source, settings_row)

        assert recorded, "retry was never called"
        countdown = recorded.get("countdown")
        assert 120 <= countdown <= 300, f"countdown {countdown} out of [120,300]"


# ═════════════════════════════════════════════════════════════════════════════
# G2 — _crawl_source sets post.image_source_urls before save_post_images.delay
# ═════════════════════════════════════════════════════════════════════════════

def _make_media(pk="123", media_type=1, code="abc", taken_at=None, url="https://cdn.ig/img.jpg"):
    """Minimal instagrapi-style media stub."""
    from datetime import datetime, timezone
    m = MagicMock()
    m.pk = pk
    m.media_type = media_type
    m.code = code
    m.taken_at = taken_at or datetime(2026, 10, 5, 10, tzinfo=timezone.utc)
    m.caption_text = "caption"
    m.resources = []
    m.thumbnail_url = url
    m.url = url
    return m


class TestCrawlSourceImageSourceUrls:
    """_crawl_source must set post.image_source_urls = image_urls before commit."""

    def _run(self, media_type=1, album_indices=None):
        from app.tasks.crawler import _crawl_source
        from app.models.ig_sources import IGSource, ScraperBackend
        from app.models.settings import Settings as DBSettings
        from app.models.posts import Post, PostStatus, MediaType

        media = _make_media(media_type=media_type)

        source = MagicMock()
        source.id = 1
        source.ig_username = "testuser"
        source.is_active = True
        source.scraper_backend = ScraperBackend.viewer
        source.last_seen_post_id = None
        source.album_image_indices = album_indices

        created_posts = []

        class CapturingDB:
            def __init__(self):
                self._call_count = {}

            def query(self, model):
                q = MagicMock()
                if model is IGSource:
                    q.filter_by.return_value.first.return_value = source
                elif model is Post:
                    q.filter_by.return_value.first.return_value = None
                elif model is DBSettings:
                    settings = MagicMock()
                    settings.max_post_age_days = 30
                    q.filter_by.return_value.first.return_value = settings
                else:
                    q.filter_by.return_value.first.return_value = None
                return q

            def add(self, obj):
                if isinstance(obj, Post):
                    obj.id = 99
                    created_posts.append(obj)

            def flush(self):
                pass

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        db = CapturingDB()

        saved_args = {}

        def fake_delay(post_id, image_urls):
            saved_args["post_id"] = post_id
            saved_args["image_urls"] = image_urls

        with (
            patch("app.tasks.crawler._fetch_medias", return_value=[media]),
            patch("app.tasks.image_saver.save_post_images.delay", side_effect=fake_delay),
        ):
            _crawl_source(db, source_id=1)

        return created_posts, saved_args

    def test_image_post_has_image_source_urls(self):
        """Single image post: image_source_urls populated with the CDN URL."""
        posts, saved = self._run(media_type=1)
        assert posts, "no post created"
        post = posts[0]
        assert post.image_source_urls == ["https://cdn.ig/img.jpg"]
        assert saved["image_urls"] == post.image_source_urls

    def test_image_source_urls_matches_delay_args(self):
        """image_source_urls on the Post equals what was passed to save_post_images.delay."""
        posts, saved = self._run(media_type=1)
        assert posts[0].image_source_urls == saved["image_urls"]


# ═════════════════════════════════════════════════════════════════════════════
# 4. Beat schedule & Celery import checks (no burner tasks)
# ═════════════════════════════════════════════════════════════════════════════

class TestBeatScheduleAndImports:

    def test_no_burner_tasks_in_beat_schedule(self):
        from app.tasks.celery_app import celery_app
        beat_keys = set(celery_app.conf.beat_schedule.keys())
        deleted_entries = {
            "reset-burner-counters",
            "post-burner-stories",
            "post-burner-comments",
            "warmup-burners",
        }
        for entry in deleted_entries:
            assert entry not in beat_keys, f"Deleted beat entry {entry} still in schedule"

    def test_all_beat_schedule_tasks_registered_and_importable(self):
        from app.tasks.celery_app import celery_app
        import app.main  # noqa: F401
        for include_mod in celery_app.conf.include:
            __import__(include_mod)
        for name, entry in celery_app.conf.beat_schedule.items():
            task_name = entry["task"]
            assert task_name in celery_app.tasks, f"Beat entry {name} task {task_name} is not registered in celery_app.tasks"

    def test_no_deleted_modules_in_celery_include(self):
        from app.tasks.celery_app import celery_app
        deleted_modules = {
            "app.tasks.story_poster",
            "app.tasks.comment_poster",
            "app.tasks.warmup",
        }
        for mod in deleted_modules:
            assert mod not in celery_app.conf.include, f"Deleted task module {mod} still in celery include"

