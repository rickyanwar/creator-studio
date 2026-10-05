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


def _make_source(username="testuser", scraper_backend=None, burner_account_id=None):
    src = MagicMock()
    src.ig_username = username
    src.scraper_backend = scraper_backend
    src.burner_account_id = burner_account_id
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

    def test_global_instagrapi_returns_instagrapi(self):
        db = _make_db(global_scraper_mode="instagrapi")
        result = _resolve_effective_backend(ScraperBackend.viewer, db)
        assert result == ScraperBackend.instagrapi

    def test_global_auto_per_source_flashapi_returns_viewer(self):
        db = _make_db(global_scraper_mode="auto")
        result = _resolve_effective_backend(ScraperBackend.flashapi, db)
        assert result == ScraperBackend.viewer

    def test_global_auto_per_source_auto_returns_auto(self):
        db = _make_db(global_scraper_mode="auto")
        result = _resolve_effective_backend(ScraperBackend.auto, db)
        assert result == ScraperBackend.auto

    def test_missing_settings_row_returns_per_source(self):
        """No Settings row → global_mode defaults to 'auto' → per-source wins."""
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        result = _resolve_effective_backend(ScraperBackend.instagrapi, db)
        assert result == ScraperBackend.instagrapi

    def test_missing_settings_row_per_source_none_returns_auto(self):
        """No Settings row, per-source None → auto."""
        db = MagicMock()
        db.query.return_value.filter_by.return_value.first.return_value = None
        result = _resolve_effective_backend(None, db)
        assert result == ScraperBackend.auto


# ═════════════════════════════════════════════════════════════════════════════
# 2. _fetch_medias
# ═════════════════════════════════════════════════════════════════════════════

def _make_burner(requests_today=0):
    b = MagicMock()
    from app.models.burner_accounts import BurnerStatus
    b.status = BurnerStatus.active
    b.requests_today = requests_today
    b.ig_username = "burner_account"
    return b


class TestFetchMediasAuto:

    def _db_with_burner(self, burner):
        """DB that returns global_mode=auto and one available burner."""
        db = MagicMock()

        settings_row = MagicMock()
        settings_row.scraper_mode = "auto"

        from app.models.settings import Settings as DBSettings
        from app.models.burner_accounts import BurnerAccount

        def _query(model):
            q = MagicMock()
            if model is DBSettings:
                q.filter_by.return_value.first.return_value = settings_row
            elif model is BurnerAccount:
                # filter().all() returns [burner]
                q.filter.return_value.all.return_value = [burner] if burner else []
                q.filter_by.return_value.first.return_value = None
            else:
                q.filter_by.return_value.first.return_value = None
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = _query
        return db

    def _db_no_burner(self):
        return self._db_with_burner(None)

    def test_auto_with_burner_uses_session_manager_not_viewer(self):
        """auto + available burner → IGSessionManager used, viewer NOT called."""
        burner = _make_burner()
        db = self._db_with_burner(burner)
        source = _make_source()
        source.burner_account_id = None  # no pre-assigned burner

        fake_medias = [MagicMock()]
        mock_manager = MagicMock()
        mock_manager.fetch_recent_posts.return_value = fake_medias
        mock_manager.client.media_like.side_effect = Exception("skip")

        fetch_mock = MagicMock(return_value=[])  # viewer should NOT be called

        # IGSessionManager is imported lazily inside _fetch_medias — patch at source
        with (
            patch("app.services.ig_session_manager.IGSessionManager", return_value=mock_manager),
            patch.object(
                _viewer_mod,
                "fetch_recent_posts",
                fetch_mock,
            ),
            patch("random.random", return_value=1.0),  # skip warmup like
        ):
            result = _fetch_medias(source, ScraperBackend.auto, db, 10)

        assert result == fake_medias
        fetch_mock.assert_not_called()

    def test_auto_no_burner_calls_viewer_and_clears_error(self):
        """auto + no burner → viewer fetch_recent_posts called, last_crawl_error = None."""
        db = self._db_no_burner()
        source = _make_source()
        source.burner_account_id = None

        fake_medias = [MagicMock(), MagicMock()]
        fetch_mock = MagicMock(return_value=fake_medias)

        with patch.object(
            _viewer_mod,
            "fetch_recent_posts",
            fetch_mock,
        ):
            result = _fetch_medias(source, ScraperBackend.auto, db, 10)

        fetch_mock.assert_called_once_with(source.ig_username, amount=10)
        assert result == fake_medias
        assert source.last_crawl_error is None


class TestFetchMediasInstagrapi:

    def _db_no_burner(self):
        db = MagicMock()
        settings_row = MagicMock()
        settings_row.scraper_mode = "auto"  # won't override instagrapi per-source

        from app.models.settings import Settings as DBSettings
        from app.models.burner_accounts import BurnerAccount

        def _query(model):
            q = MagicMock()
            if model is DBSettings:
                q.filter_by.return_value.first.return_value = settings_row
            elif model is BurnerAccount:
                q.filter.return_value.all.return_value = []
                q.filter_by.return_value.first.return_value = None
            else:
                q.filter_by.return_value.first.return_value = None
                q.filter.return_value.all.return_value = []
            return q

        db.query.side_effect = _query
        return db

    def test_instagrapi_no_burner_returns_empty_and_sets_error(self):
        """instagrapi + no burner → [] and last_crawl_error set, viewer NOT called."""
        db = self._db_no_burner()
        source = _make_source()
        source.burner_account_id = None

        fetch_mock = MagicMock(return_value=[MagicMock()])  # should not be called

        with patch.object(
            _viewer_mod,
            "fetch_recent_posts",
            fetch_mock,
        ):
            result = _fetch_medias(source, ScraperBackend.instagrapi, db, 10)

        assert result == []
        assert source.last_crawl_error == "No active burner available"
        fetch_mock.assert_not_called()


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
