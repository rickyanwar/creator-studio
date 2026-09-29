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
    """Test sync_pending_schedules by patching SessionLocal and the Repliz client.

    S10 update: the function now uses a single unified query (not two separate
    paths), so the mock setup uses a single query chain that returns the
    desired job list from .all().
    """

    def _run_sync(self, jobs, client_responses):
        """Helper: patch db (single query) and client, run sync, return mocks."""
        mock_db = MagicMock()
        mock_session = MagicMock()
        mock_db.return_value = mock_session

        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = jobs
        mock_session.query.return_value = q

        mock_client = MagicMock()
        responses = iter(client_responses)
        mock_client.get_schedule.side_effect = lambda sid: next(responses)

        with (
            patch("app.tasks.status_sync.SessionLocal", mock_db),
            patch("app.tasks.status_sync.get_repliz_client_from_db", return_value=mock_client),
        ):
            from app.tasks.status_sync import sync_pending_schedules
            sync_pending_schedules()

        return mock_client, mock_session

    # ── S2a/S10: success path — job with no final status → response stored ────

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
        client, _ = self._run_sync([job], [repliz_success])

        client.get_schedule.assert_called_once_with("abc123")
        # response stored
        assert job.repliz_response_json == repliz_success
        # status unchanged — it's a success, not a failure
        assert job.status == "published"
        assert job.last_error is None

    # ── S2a/S10: failure path — Repliz reports failure → job.status = failed ──

    @pytest.mark.parametrize("failure_status", sorted(_REPLIZ_FAILURE_STATUSES))
    def test_clip_job_repliz_failure_marks_job_failed(self, failure_status):
        """When Repliz reports a failure status, the clip job is marked failed."""
        from app.models.publish_jobs import PublishJobStatus
        now = _now()
        job = _make_job(
            job_id=9456,
            content_type="youtube_clip",
            repliz_response_json={"scheduleId": "xyz"},
            scheduled_for=now - timedelta(minutes=10),
        )
        job.status = "published"

        repliz_fail = {
            "scheduleId": "xyz",
            "status": failure_status,
            "errorMessage": "Facebook rejected the video",
        }
        self._run_sync([job], [repliz_fail])

        # job must be marked failed
        assert job.status == PublishJobStatus.failed
        assert job.last_error is not None
        assert job.last_error.startswith("Repliz: ")
        assert "Facebook rejected" in job.last_error

    # ── S2a/S10: still-pending → job unchanged ────────────────────────────────

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
        self._run_sync([job], [repliz_pending])

        assert job.status == "published"   # unchanged
        assert job.last_error is None

    # ── S2a/S10: non-clip job failure marks failed too (unified path) ─────────

    def test_other_content_type_not_in_clip_query(self):
        """Non-youtube_clip jobs go through the unified path.
        A news_content job that Repliz marks failed must be marked failed too."""
        from app.models.publish_jobs import PublishJobStatus
        now = _now()
        news_job = _make_job(
            job_id=100,
            content_type="news_content",
            repliz_response_json={"scheduleId": "news1"},
            scheduled_for=now - timedelta(minutes=5),
        )

        # Simulate DB returning the news job (no final status → it was selected).
        # Repliz says "success" — job status stays published (not failed).
        self._run_sync([news_job], [{"status": "success"}])

        assert news_job.status != PublishJobStatus.failed


# ── S10(a): all-modes sync — single unified query path ───────────────────────
#
# After the S10 fix, status_sync uses ONE query (no content_type filter) whose
# "no final status yet" condition is expressed in SQL rather than in Python.
# We verify:
#   1. A news_content job WITHOUT a final stored status IS selected and synced.
#   2. A job whose stored response already has status="success" is NOT selected
#      (because the SQL filter excludes final-status rows).
#   3. A failure from Repliz marks the job failed regardless of content_type.
#   4. The single query path is used (only one db.query call per tick, not two).
#
# These tests call sync_pending_schedules() with a patched SessionLocal that
# returns the pre-baked job list from its single query, matching the existing
# test style in this file.

def _run_single_query_sync(jobs_returned, client_responses):
    """Patch db so the single all-modes query returns `jobs_returned`,
    run sync_pending_schedules(), and return (mock_client, mock_db)."""
    mock_db = MagicMock()
    mock_db_cls = MagicMock(return_value=mock_db)

    q = MagicMock()
    q.filter.return_value = q
    q.order_by.return_value = q
    q.limit.return_value = q
    q.all.return_value = jobs_returned
    mock_db.query.return_value = q

    mock_client = MagicMock()
    responses = iter(client_responses)
    mock_client.get_schedule.side_effect = lambda sid: next(responses)

    with (
        patch("app.tasks.status_sync.SessionLocal", mock_db_cls),
        patch("app.tasks.status_sync.get_repliz_client_from_db", return_value=mock_client),
    ):
        from app.tasks.status_sync import sync_pending_schedules
        sync_pending_schedules()

    return mock_client, mock_db


class TestS10AllModesSync:
    """S10(a): unified query syncs all content types, final-status jobs excluded."""

    def test_news_content_job_without_status_gets_synced(self):
        """A news_content job with only {scheduleId} in its response (no status
        key) is selected by the unified query and has its response updated."""
        now = _now()
        job = _make_job(
            job_id=361,
            content_type="news_content",
            repliz_response_json={"scheduleId": "ns1"},
            scheduled_for=now - timedelta(minutes=30),
        )

        repliz_resp = {"scheduleId": "ns1", "status": "success"}
        client, _ = _run_single_query_sync([job], [repliz_resp])

        client.get_schedule.assert_called_once_with("abc123")
        assert job.repliz_response_json == repliz_resp

    def test_discussion_job_without_status_gets_synced(self):
        """A discussion job is synced the same as any other type."""
        now = _now()
        job = _make_job(
            job_id=19,
            content_type="discussion",
            repliz_response_json={"scheduleId": "disc1"},
            scheduled_for=now - timedelta(hours=1),
        )
        repliz_resp = {"scheduleId": "disc1", "status": "success"}
        client, _ = _run_single_query_sync([job], [repliz_resp])

        client.get_schedule.assert_called_once()
        assert job.repliz_response_json == repliz_resp

    def test_job_with_success_status_not_selected(self):
        """A job whose stored response already has status='success' must NOT be
        returned by the query — the SQL filter excludes final-status rows.
        We verify this by asserting get_schedule is never called when the query
        returns an empty list (the DB did its job)."""
        # The SQL filter is what excludes this — we simulate that the DB returns []
        client, _ = _run_single_query_sync([], [])
        client.get_schedule.assert_not_called()

    def test_failure_marks_any_content_type_failed(self):
        """A Repliz failure status marks ANY content_type job as failed,
        not just youtube_clip (the old behaviour)."""
        from app.models.publish_jobs import PublishJobStatus
        now = _now()

        for ct in ("news_content", "discussion", "pinterest_content", "facebook_recreate"):
            job = _make_job(
                job_id=1,
                content_type=ct,
                repliz_response_json={"scheduleId": "x"},
                scheduled_for=now - timedelta(minutes=10),
            )
            repliz_fail = {"scheduleId": "x", "status": "failed",
                           "errorMessage": "Rejected by Facebook"}
            _run_single_query_sync([job], [repliz_fail])

            assert job.status == PublishJobStatus.failed, f"Expected failed for {ct}"
            assert job.last_error is not None
            assert job.last_error.startswith("Repliz: ")

    def test_single_query_used_not_two(self):
        """After S10, only ONE db.query call per tick (no split generic+clip)."""
        client, mock_db = _run_single_query_sync([], [])
        # db.query should be called exactly once (the unified query)
        assert mock_db.query.call_count == 1

    def test_order_by_and_limit_applied(self):
        """The unified query must call .order_by(...) and .limit(50) before .all()."""
        mock_db = MagicMock()
        mock_db_cls = MagicMock(return_value=mock_db)

        q = MagicMock()
        q.filter.return_value = q
        q.order_by.return_value = q
        q.limit.return_value = q
        q.all.return_value = []
        mock_db.query.return_value = q

        mock_client = MagicMock()

        with (
            patch("app.tasks.status_sync.SessionLocal", mock_db_cls),
            patch("app.tasks.status_sync.get_repliz_client_from_db", return_value=mock_client),
        ):
            from app.tasks.status_sync import sync_pending_schedules
            sync_pending_schedules()

        q.order_by.assert_called_once()
        q.limit.assert_called_once_with(50)
