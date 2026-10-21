"""A credits-API auth error in a heartbeat must not mark a working Flow account as logged out."""
import pytest

from app.database import Base, SessionLocal, engine
from app.models.ai_video import GflowAccount, GflowWorker, VideoIdea, VideoJob
from app.services import ai_video_dispatch


@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()

    def clean():
        session.query(VideoJob).delete()
        session.query(VideoIdea).delete()
        session.query(GflowAccount).delete()
        session.query(GflowWorker).delete()
        session.commit()

    try:
        clean()
        yield session
    finally:
        clean()
        session.close()


def _beat(db, **account):
    ai_video_dispatch.record_heartbeat(db, "w1", {"version": "1", "max_concurrent": 1, "running_job_ids": [],
                                                  "accounts": [dict(profile="russel", **account)]})
    return db.query(GflowAccount).filter_by(worker_id="w1", profile="russel").one()


def test_credits_api_auth_error_keeps_the_account_logged_in(db_session):
    db_session.add(GflowAccount(worker_id="w1", profile="russel", authenticated=True, status="ok", credits=1050))
    db_session.commit()

    acc = _beat(db_session, authenticated=False, credits=None, error="aisandbox-pa authentication failed",
                error_class="AuthError")

    assert acc.authenticated is True
    assert acc.status == "ok"
    assert acc.credits == 1050


def test_credits_api_auth_error_still_updates_reported_credits(db_session):
    db_session.add(GflowAccount(worker_id="w1", profile="russel", authenticated=True, status="ok", credits=1050))
    db_session.commit()

    acc = _beat(db_session, authenticated=False, credits=990, error="aisandbox-pa authentication failed")

    assert acc.status == "ok"
    assert acc.credits == 990


def test_other_auth_error_still_marks_auth_error(db_session):
    db_session.add(GflowAccount(worker_id="w1", profile="russel", authenticated=True, status="ok", credits=1050))
    db_session.commit()

    acc = _beat(db_session, authenticated=False, credits=None, error="session expired, sign in again")

    assert acc.authenticated is False
    assert acc.status == "auth_error"
