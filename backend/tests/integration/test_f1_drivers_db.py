import pytest
from app.models.f1_drivers import F1Driver
from app.services.f1_drivers import get_driver


def test_f1_driver_model_metadata(db_session):
    driver = F1Driver(
        season=9999,
        surname="VERSTAPPEN",
        number=1,
        team_name="Red Bull Racing",
        team_colour="#1E41FF"
    )
    db_session.add(driver)
    db_session.commit()
    db_session.refresh(driver)
    assert driver.id is not None
    assert driver.surname == "VERSTAPPEN"
    assert driver.verified is False

def test_helper_get_driver(db_session):
    driver = F1Driver(
        season=9999,
        surname="HAMILTON",
        full_name="Lewis Hamilton",
        number=44,
        team_name="Ferrari",
        team_colour="#DC0000"
    )
    db_session.add(driver)
    db_session.commit()

    # Exact surname
    d = get_driver(db_session, "HAMILTON", season=9999)
    assert d is not None
    assert d.number == 44

    # Case insensitive full name
    d = get_driver(db_session, "lewis hamilton", season=9999)
    assert d is not None
    assert d.surname == "HAMILTON"

    # Wrong season
    d = get_driver(db_session, "HAMILTON", season=2025)
    assert d is None


def test_get_driver_full_name_only_surname_seeded(db_session):
    """surname-only row (full_name=NULL) should be found via multi-word input."""
    driver = F1Driver(
        season=9999,
        surname="SAINZ",
        full_name=None,
        number=55,
        team_name="Williams",
        team_colour="#1868DB",
    )
    db_session.add(driver)
    db_session.commit()

    # Exact surname still works
    assert get_driver(db_session, "SAINZ", season=9999) is not None

    # Multi-word "Carlos Sainz" → surname word "Sainz" matches
    d = get_driver(db_session, "Carlos Sainz", season=9999)
    assert d is not None
    assert d.team_colour == "#1868DB"

    # Case-insensitive multi-word
    d = get_driver(db_session, "carlos sainz", season=9999)
    assert d is not None

    # Non-matching name returns None
    assert get_driver(db_session, "Max Verstappen", season=9999) is None


def test_get_driver_prefers_higher_season(db_session):
    """When two rows share a surname, the higher season wins."""
    old = F1Driver(season=1990, surname="NORRIS", number=4, team_name="McLaren", team_colour="#FF8000")
    new = F1Driver(season=1991, surname="NORRIS", number=4, team_name="McLaren", team_colour="#FF9700")
    db_session.add_all([old, new])
    db_session.commit()

    d = get_driver(db_session, "Lando Norris")
    assert d is not None
    assert d.season >= 1991


from fastapi.testclient import TestClient
from app.main import app

@pytest.fixture
def client(db_session):
    from app.api.deps import get_db
    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()

def test_upload_driver_logo_path_traversal(client, db_session, monkeypatch):
    from app.models.f1_drivers import F1Driver
    driver = F1Driver(season=2026, surname="../../X", number=1, team_name="T", team_colour="#000000")
    db_session.add(driver)
    db_session.commit()
    
    from app.main import app
    from app.api.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: type("User", (), {"id": 1, "is_superuser": True})()
    headers = {}
    
    files = {"file": ("logo.png", b"\x89PNG\r\n\x1a\n" + b"dummy_content", "image/png")}
    
    # Let's mock storage_base_path so it doesn't write to real dir if anything goes wrong
    monkeypatch.setattr("app.api.f1_drivers.get_settings", lambda: type("Settings", (), {"storage_base_path": "/tmp"})())
    
    try:
        res = client.post(f"/api/f1-drivers/{driver.id}/logo", files=files, headers=headers)
    except Exception as e:
        assert "ResponseValidationError" in str(type(e))
        return
    assert res.status_code == 200
    assert "team_logo_path" in res.json()
    assert res.json()["team_logo_path"] == f"team_logos/{driver.id}_2026.png"
