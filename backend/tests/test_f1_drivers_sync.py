import pytest
from unittest.mock import MagicMock, patch
import httpx
from sqlalchemy.orm import Session
from app.models.f1_drivers import F1Driver
from app.services.f1_drivers_sync import fetch_openf1_drivers, sync_f1_drivers
from app.tasks.celery_app import celery_app
from app.database import Base, engine, SessionLocal
from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture(scope="module")
def setup_db():
    Base.metadata.create_all(bind=engine)
    yield

@pytest.fixture
def db_session(setup_db):
    session = SessionLocal()
    try:
        # Clean up any f1_drivers created in tests
        session.query(F1Driver).delete()
        session.commit()
        yield session
    finally:
        session.rollback()
        session.close()

MOCK_SESSIONS_JSON = [
    {
        "session_key": 11388,
        "session_name": "Race",
        "year": 2026,
    }
]

MOCK_DRIVERS_JSON = [
    {
        "session_key": 11388,
        "driver_number": 6,
        "last_name": "Hadjar",
        "full_name": "Isack HADJAR",
        "team_name": "Red Bull Racing",
        "team_colour": "4781D7",
    },
    {
        "session_key": 11388,
        "driver_number": 41,
        "last_name": "Lindblad",
        "full_name": "Arvid LINDBLAD",
        "team_name": "Racing Bulls",
        "team_colour": "6c98ff",
    },
    {
        "session_key": 11388,
        "driver_number": None,
        "last_name": "MissingNum",
        "full_name": "Missing Number",
        "team_name": "Some Team",
        "team_colour": "000000",
    },
    {
        "session_key": 11388,
        "driver_number": 99,
        "last_name": "MissingTeam",
        "full_name": "Missing Team",
        "team_name": None,
        "team_colour": "000000",
    },
]


def test_mapping_from_canned_openf1_json():
    def mock_get(url, **kwargs):
        resp = MagicMock()
        resp.raise_for_status.return_value = None
        if "sessions" in str(url):
            resp.json.return_value = MOCK_SESSIONS_JSON
        elif "drivers" in str(url):
            resp.json.return_value = MOCK_DRIVERS_JSON
        return resp

    mock_client = MagicMock()
    mock_client.get.side_effect = mock_get

    mapped = fetch_openf1_drivers(mock_client)
    assert len(mapped) == 2

    hadjar = next(d for d in mapped if d["surname"] == "HADJAR")
    assert hadjar["season"] == 2026
    assert hadjar["surname"] == "HADJAR"
    assert hadjar["full_name"] == "Isack Hadjar"
    assert hadjar["number"] == 6
    assert hadjar["team_name"] == "Red Bull Racing"
    assert hadjar["team_colour"] == "#4781D7"

    lindblad = next(d for d in mapped if d["surname"] == "LINDBLAD")
    assert lindblad["season"] == 2026
    assert lindblad["surname"] == "LINDBLAD"
    assert lindblad["full_name"] == "Arvid Lindblad"
    assert lindblad["number"] == 41
    assert lindblad["team_name"] == "Racing Bulls"
    assert lindblad["team_colour"] == "#6C98FF"


def test_upsert_logic(db_session: Session):
    # Setup existing rows:
    # 1. Hadjar at old team Racing Bulls, verified=False, team_logo_path set
    # 2. Reserve driver Doohan not in incoming feed
    old_hadjar = F1Driver(
        season=2026,
        surname="HADJAR",
        full_name="Isack Hadjar",
        number=6,
        team_name="Racing Bulls",
        team_colour="#4781D7",
        team_logo_path="team_logos/hadjar_logo.png",
        verified=False,
    )
    reserve = F1Driver(
        season=2026,
        surname="DOOHAN",
        full_name="Jack Doohan",
        number=61,
        team_name="Alpine",
        team_colour="#0093CC",
        team_logo_path=None,
        verified=False,
    )
    db_session.add_all([old_hadjar, reserve])
    db_session.commit()

    incoming = [
        {
            "season": 2026,
            "surname": "HADJAR",
            "full_name": "Isack Hadjar",
            "number": 6,
            "team_name": "Red Bull Racing",
            "team_colour": "#4781D7",
        },
        {
            "season": 2026,
            "surname": "LINDBLAD",
            "full_name": "Arvid Lindblad",
            "number": 41,
            "team_name": "Racing Bulls",
            "team_colour": "#6C98FF",
        },
    ]

    counts = sync_f1_drivers(db_session, incoming)
    assert counts == {"inserted": 1, "updated": 1, "unchanged": 0}

    # Verify Hadjar updated, logo preserved, verified=True
    hadjar = db_session.query(F1Driver).filter_by(season=2026, surname="HADJAR").first()
    assert hadjar.team_name == "Red Bull Racing"
    assert hadjar.team_logo_path == "team_logos/hadjar_logo.png"
    assert hadjar.verified is True

    # Verify Lindblad inserted, verified=True
    lindblad = db_session.query(F1Driver).filter_by(season=2026, surname="LINDBLAD").first()
    assert lindblad is not None
    assert lindblad.number == 41
    assert lindblad.team_name == "Racing Bulls"
    assert lindblad.verified is True

    # Verify Doohan untouched
    doohan = db_session.query(F1Driver).filter_by(season=2026, surname="DOOHAN").first()
    assert doohan is not None
    assert doohan.verified is False

    # Second sync: no changes
    counts_second = sync_f1_drivers(db_session, incoming)
    assert counts_second == {"inserted": 0, "updated": 0, "unchanged": 2}


def test_network_error_no_db_change(db_session: Session):
    from app.tasks.f1_drivers_sync import sync_f1_drivers_task

    # Seed one driver
    driver = F1Driver(
        season=2026,
        surname="LECLERC",
        full_name="Charles Leclerc",
        number=16,
        team_name="Ferrari",
        team_colour="#DC0000",
        verified=False,
    )
    db_session.add(driver)
    db_session.commit()

    with patch("httpx.Client") as mock_client_cls:
        mock_instance = mock_client_cls.return_value.__enter__.return_value
        mock_instance.get.side_effect = httpx.RequestError("Network error")
        # Run task
        res = sync_f1_drivers_task()
        assert res is None or res.get("status") == "error"

    # DB untouched
    d = db_session.query(F1Driver).filter_by(season=2026, surname="LECLERC").first()
    assert d.verified is False


def test_endpoint_returns_counts(db_session: Session):
    from app.api.deps import get_db, get_current_user

    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: type("User", (), {"id": 1, "is_superuser": True})()

    with patch("app.services.f1_drivers_sync.fetch_openf1_drivers") as mock_fetch:
        mock_fetch.return_value = [
            {
                "season": 2026,
                "surname": "VERSTAPPEN",
                "full_name": "Max Verstappen",
                "number": 3,
                "team_name": "Red Bull Racing",
                "team_colour": "#4781D7",
            }
        ]
        with TestClient(app) as client:
            resp = client.post("/f1-drivers/sync")
            assert resp.status_code == 200
            data = resp.json()
            assert "inserted" in data
            assert "updated" in data
            assert "unchanged" in data

    app.dependency_overrides.clear()


def test_celery_beat_entry_and_task_registered():
    assert "app.tasks.f1_drivers_sync" in celery_app.conf.include
    entry = celery_app.conf.beat_schedule.get("sync-f1-drivers-weekly")
    assert entry is not None
    assert entry["task"] == "app.tasks.f1_drivers_sync.sync_f1_drivers_task"
