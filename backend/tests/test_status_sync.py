"""Unit tests for S2a — status_sync.py Mode 7 clip sync fix.

Tests run with no database; all model/db calls are mocked.
pytest -q backend/tests/test_status_sync.py
"""

import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime, timedelta, timezone


# ── helpers ──────────────────────────────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_job(
    job_id: int = 1,
    content_type: str = "youtube_clip",
    status: str = "published",
    repliz_schedule_id: str = "abc123",
    repliz_response_json: dict | None = None,
    scheduled_for: datetime | None = None,
):
    """Build a minimal mock PublishJob."""
    job = MagicMock()
    job.id = job_id
    job.content_type = content_type
    job.status = status
    job.repliz_schedule_id = repliz_schedule_id
    job.repliz_response_json = repliz_response_json
    job.scheduled_for = scheduled_for or (_now() - timedelta(minutes=5))
    job.last_error = None
    return job


# ── import helpers from status_sync (pure functions, no db) ──────────────────

from app.tasks.status_sync import (
    _best_error_message,
    _response_has_final_status,
    _response_needs_sync,
    _REPLIZ_FAILURE_STATUSES,
    _REPLIZ_PENDING_STATUSES,
)


# ── _best_error_message ───────────────────────────────────────────────────────

class TestBestErrorMessage:
    def test_prefers_error_message_key(self):
        data = {"errorMessage": "Upload rejected by Facebook", "status": "failed"}
        assert "Upload rejected by Facebook" in _best_error_message(data)

    def test_falls_back_to_message(self):
        data = {"message": "Video too long", "status": "error"}
        assert "Video too long" in _best_error_message(data)

    def test_truncates_at_2000(self):
        data = {"message": "x" * 3000}
        assert len(_best_error_message(data)) <= 2000

    def test_fallback_to_str_repr(self):
        data = {"status": "failed", "code": 42}
        result = _best_error_message(data)
        assert isinstance(result, str)


# ── _response_has_final_status / _response_needs_sync ───────────────────────

class TestResponseStatusHelpers:
    def test_no_status_key_needs_sync(self):
        assert _response_needs_sync({"scheduleId": "abc"}) is True

    def test_pending_needs_sync(self):
        assert _response_needs_sync({"status": "pending"}) is True

    def test_processing_needs_sync(self):
        assert _response_needs_sync({"status": "processing"}) is True

    def test_queued_needs_sync(self):
        assert _response_needs_sync({"status": "queued"}) is True

    def test_scheduled_needs_sync(self):
        assert _response_needs_sync({"status": "scheduled"}) is True

    def test_success_has_final_status(self):
        assert _response_has_final_status({"status": "success"}) is True

    def test_failed_has_final_status(self):
        assert _response_has_final_status({"status": "failed"}) is True

    def test_none_needs_sync(self):
        assert _response_needs_sync(None) is True

    def test_empty_dict_needs_sync(self):
        assert _response_needs_sync({}) is True

    def test_success_does_not_need_sync(self):
        assert _response_needs_sync({"status": "success"}) is False


# ── sync_pending_schedules (integration-style with full mocking) ─────────────

class TestSyncPendingSchedules:
    """Test sync_pending_schedules by patching SessionLocal and the Repliz client."""

    def _run_sync(self, generic_jobs, clip_jobs, client_responses):
        """Helper: patch db and client, run sync, return (client, db session) mocks."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.return_value = mock_session

        # We need to mock the chained SQLAlchemy query builder per call.
        # Call 1: generic jobs query  Call 2: clip jobs query
        call_count = [0]

        def query_side_effect(model):
            q = MagicMock()
            q.filter.return_value = q
            q.limit.return_value = q
            if call_count[0] == 0:
                q.all.return_value = generic_jobs
            else:
                q.all.return_value = clip_jobs
            call_count[0] += 1
            return q

        mock_session.query.side_effect = query_side_effect

        mock_client = MagicMock()
        responses = iter(client_responses)
        mock_client.get_schedule.side_effect = lambda sid: next(responses)

        with (
            patch("app.tasks.status_sync.SessionLocal", mock_db),
            patch("app.tasks.status_sync.get_repliz_client_from_db", return_value=mock_client),
            # Patch ContentType and PublishJobStatus used inside the function
            patch("app.tasks.status_sync.datetime") as mock_dt,
        ):
            from app.tasks.status_sync import sync_pending_schedules
            from app.models.publish_jobs import ContentType, PublishJobStatus
            # Patch datetime.now to return a fixed time
            now = datetime(2026, 9, 28, 14, 0, 0)
            mock_dt.now.return_value = now
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)

            # Import and re-import inside patch context is tricky; instead
            # call the actual function with patched db
            sync_pending_schedules()

        return mock_client, mock_session

    # ── S2a: success path — clip job with no final status → response stored ──

    def test_clip_job_success_stores_response(self):
        """A clip job whose Repliz response has no status key gets its
        response stored and job status stays published."""
        now = _now()
        job = _make_job(
            job_id=9307,
            content_type="youtube_clip",
            repliz_response_json={"scheduleId": "abc"},
            scheduled_for=now - timedelta(minutes=10),
        )

        repliz_success = {"scheduleId": "abc", "status": "success"}

        with (
            patch("app.tasks.status_sync.SessionLocal") as mock_db_cls,
            patch("app.tasks.status_sync.get_repliz_client_from_db") as mock_get_client,
        ):
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db

            # Generic query returns empty; clip query returns our job
            generic_q = MagicMock()
            generic_q.filter.return_value = generic_q
            generic_q.limit.return_value = generic_q
            generic_q.all.return_value = []

            clip_q = MagicMock()
            clip_q.filter.return_value = clip_q
            clip_q.limit.return_value = clip_q
            clip_q.all.return_value = [job]

            call_n = [0]
            def query_side(model):
                q = generic_q if call_n[0] == 0 else clip_q
                call_n[0] += 1
                return q
            mock_db.query.side_effect = query_side

            mock_client = MagicMock()
            mock_client.get_schedule.return_value = repliz_success
            mock_get_client.return_value = mock_client

            from app.tasks.status_sync import sync_pending_schedules
            sync_pending_schedules()

        mock_client.get_schedule.assert_called_once_with("abc123")
        # response stored
        assert job.repliz_response_json == repliz_success
        # status unchanged — it's a success, not a failure
        assert job.status == "published"
        assert job.last_error is None

    # ── S2a: failure path — Repliz reports "failed" → job.status = failed ────

    @pytest.mark.parametrize("failure_status", sorted(_REPLIZ_FAILURE_STATUSES))
    def test_clip_job_repliz_failure_marks_job_failed(self, failure_status):
        """When Repliz reports a failure status, the clip job is marked failed."""
        now = _now()
        job = _make_job(
            job_id=9456,
            content_type="youtube_clip",
            repliz_response_json={"scheduleId": "xyz"},
            scheduled_for=now - timedelta(minutes=10),
        )
        # Make job.status writeable
        job.status = "published"

        repliz_fail = {
            "scheduleId": "xyz",
            "status": failure_status,
            "errorMessage": "Facebook rejected the video",
        }

        with (
            patch("app.tasks.status_sync.SessionLocal") as mock_db_cls,
            patch("app.tasks.status_sync.get_repliz_client_from_db") as mock_get_client,
        ):
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db

            generic_q = MagicMock()
            generic_q.filter.return_value = generic_q
            generic_q.limit.return_value = generic_q
            generic_q.all.return_value = []

            clip_q = MagicMock()
            clip_q.filter.return_value = clip_q
            clip_q.limit.return_value = clip_q
            clip_q.all.return_value = [job]

            call_n = [0]
            def query_side(model):
                q = generic_q if call_n[0] == 0 else clip_q
                call_n[0] += 1
                return q
            mock_db.query.side_effect = query_side

            mock_client = MagicMock()
            mock_client.get_schedule.return_value = repliz_fail
            mock_get_client.return_value = mock_client

            from app.tasks.status_sync import sync_pending_schedules
            # Import ContentType to patch it inside the function module
            from app.models.publish_jobs import ContentType, PublishJobStatus
            sync_pending_schedules()

        # job must be marked failed
        assert job.status == PublishJobStatus.failed
        assert job.last_error is not None
        assert job.last_error.startswith("Repliz: ")
        assert "Facebook rejected" in job.last_error

    # ── S2a: still-pending → job unchanged ───────────────────────────────────

    def test_clip_job_still_pending_unchanged(self):
        """A clip job whose Repliz status is still 'pending' is polled but
        the job's own status stays published."""
        now = _now()
        job = _make_job(
            job_id=9337,
            content_type="youtube_clip",
            repliz_response_json={"scheduleId": "def"},
            scheduled_for=now - timedelta(minutes=10),
        )

        repliz_pending = {"scheduleId": "def", "status": "pending"}

        with (
            patch("app.tasks.status_sync.SessionLocal") as mock_db_cls,
            patch("app.tasks.status_sync.get_repliz_client_from_db") as mock_get_client,
        ):
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db

            generic_q = MagicMock()
            generic_q.filter.return_value = generic_q
            generic_q.limit.return_value = generic_q
            generic_q.all.return_value = []

            clip_q = MagicMock()
            clip_q.filter.return_value = clip_q
            clip_q.limit.return_value = clip_q
            clip_q.all.return_value = [job]

            call_n = [0]
            def query_side(model):
                q = generic_q if call_n[0] == 0 else clip_q
                call_n[0] += 1
                return q
            mock_db.query.side_effect = query_side

            mock_client = MagicMock()
            mock_client.get_schedule.return_value = repliz_pending
            mock_get_client.return_value = mock_client

            from app.tasks.status_sync import sync_pending_schedules
            sync_pending_schedules()

        assert job.status == "published"   # unchanged
        assert job.last_error is None

    # ── S2a: non-clip job unaffected by clip path ─────────────────────────────

    def test_other_content_type_not_in_clip_query(self):
        """Non-youtube_clip jobs are NOT affected by the Mode 7 path."""
        now = _now()
        news_job = _make_job(
            job_id=100,
            content_type="news_content",
            repliz_response_json={"scheduleId": "news1"},
            scheduled_for=now - timedelta(minutes=5),
        )

        with (
            patch("app.tasks.status_sync.SessionLocal") as mock_db_cls,
            patch("app.tasks.status_sync.get_repliz_client_from_db") as mock_get_client,
        ):
            mock_db = MagicMock()
            mock_db_cls.return_value = mock_db

            generic_q = MagicMock()
            generic_q.filter.return_value = generic_q
            generic_q.limit.return_value = generic_q
            # Generic returns the news job
            generic_q.all.return_value = [news_job]

            clip_q = MagicMock()
            clip_q.filter.return_value = clip_q
            clip_q.limit.return_value = clip_q
            # Clip query returns nothing — the DB filters out non-clip jobs
            clip_q.all.return_value = []

            call_n = [0]
            def query_side(model):
                q = generic_q if call_n[0] == 0 else clip_q
                call_n[0] += 1
                return q
            mock_db.query.side_effect = query_side

            mock_client = MagicMock()
            mock_client.get_schedule.return_value = {"status": "failed"}
            mock_get_client.return_value = mock_client

            from app.tasks.status_sync import sync_pending_schedules
            from app.models.publish_jobs import PublishJobStatus
            sync_pending_schedules()

        # The news job goes through generic path, gets response stored.
        # It must NOT have its status set to failed by the clip-specific logic.
        assert news_job.status != PublishJobStatus.failed
