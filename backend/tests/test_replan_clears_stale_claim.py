"""A phone delivery that went waiting_for_device must be claimable again after device recovery."""
from datetime import datetime, timezone

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import PublishDelivery
from app.services import social_delivery_dispatch as dispatch
from tests.test_audience_schedule_integration import _create_project_and_dest, db_session  # noqa: F401


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def test_replan_after_recovery_clears_the_failed_claim(db_session, monkeypatch):  # noqa: F811
    fanpage, dest = _create_project_and_dest(db_session, name="Agung", timezone_name="Europe/London", min_gap_minutes=10)
    job = PublishJob(fanpage_id=fanpage.id, status=PublishJobStatus.pending_publish, content_type=ContentType.ig_repost)
    db_session.add(job)
    db_session.flush()
    deliv = PublishDelivery(
        publish_job_id=job.id, destination_id=dest.id, state="waiting_for_device", intended_method="phone",
        media_kind="image", claim_token="stale-token", claimed_at=datetime(2026, 10, 17, 5, 39),
        attempt_count=1, content_version=1,
    )
    db_session.add(deliv)
    db_session.commit()
    monkeypatch.setattr(dispatch, "_recovering_phone_destinations_query", lambda db, device_id=None: _Rows([dest]))

    replanned = dispatch.replan_recovering_phone_deliveries(
        db_session, device_id=1, now_utc=datetime(2026, 10, 17, 5, 40, tzinfo=timezone.utc)
    )

    assert [d.id for d in replanned] == [deliv.id]
    db_session.refresh(deliv)
    assert deliv.state == "scheduled"
    assert deliv.claim_token is None and deliv.claimed_at is None
