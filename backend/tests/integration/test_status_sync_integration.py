import os
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.target_fanpages import TargetFanpage
from app.tasks.status_sync import sync_pending_schedules

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_DATABASE_URL"),
    reason="Requires Postgres TEST_DATABASE_URL"
)

class DummySession:
    def __init__(self, session):
        self._session = session
        
    def __getattr__(self, name):
        return getattr(self._session, name)
        
    def close(self):
        pass

def test_sync_pending_schedules_integration(db_session):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    
    fp = TargetFanpage(name="Test FP", repliz_account_id="some_repliz_id_x")
    db_session.add(fp)
    db_session.flush()
    
    job_success = PublishJob(
        content_type="youtube_clip",
        fanpage_id=fp.id,
        status=PublishJobStatus.published,
        repliz_schedule_id="final_success",
        repliz_response_json={"status": "success", "postId": "1_2"},
        scheduled_for=now - timedelta(hours=1),
    )
    job_no_status = PublishJob(
        content_type="news_content",
        fanpage_id=fp.id,
        status=PublishJobStatus.published,
        repliz_schedule_id="no_status",
        repliz_response_json={"scheduleId": "no_status", "_id": 123},
        scheduled_for=now - timedelta(hours=2),
    )
    job_pending = PublishJob(
        content_type="discussion",
        fanpage_id=fp.id,
        status=PublishJobStatus.published,
        repliz_schedule_id="pending_job",
        repliz_response_json={"status": "pending"},
        scheduled_for=now - timedelta(hours=3),
    )
    job_too_new = PublishJob(
        content_type="youtube_clip",
        fanpage_id=fp.id,
        status=PublishJobStatus.published,
        repliz_schedule_id="too_new",
        repliz_response_json={"status": "pending"},
        scheduled_for=now - timedelta(minutes=1),
    )

    db_session.add_all([job_success, job_no_status, job_pending, job_too_new])
    # Committed (sync_pending_schedules reads through its own session); the
    # rows are deleted explicitly at the end of the test.
    db_session.commit()

    # Mock Repliz client
    mock_client = MagicMock()
    mock_client.get_schedule.return_value = {"status": "success"}

    # Use DummySession to prevent closing
    dummy_db = DummySession(db_session)

    with patch("app.tasks.status_sync.SessionLocal", return_value=dummy_db), \
         patch("app.tasks.status_sync.get_repliz_client_from_db", return_value=mock_client):
        sync_pending_schedules()
        
    assert mock_client.get_schedule.call_count >= 2
    
    called_ids = [call_arg[0][0] for call_arg in mock_client.get_schedule.call_args_list]
    called_ids = [cid for cid in called_ids if cid in ("no_status", "pending_job")]
    # Newest pending first -> -2 hours then -3 hours
    assert called_ids == ["no_status", "pending_job"]
    
    # Cleanup (since we committed)
    db_session.delete(job_success)
    db_session.delete(job_no_status)
    db_session.delete(job_pending)
    db_session.delete(job_too_new)
    db_session.delete(fp)
    db_session.commit()
