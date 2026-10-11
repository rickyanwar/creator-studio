import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from sqlalchemy.exc import IntegrityError
from app.main import app
from app.api.deps import get_current_user
from app.database import get_db
from app.models.f1_drivers import F1Driver


class FakeQuery:
    def __init__(self, data=None):
        self._data = data or []
    def filter(self, *args):
        return self
    def order_by(self, *args):
        return self
    def first(self):
        return self._data[0] if self._data else None
    def all(self):
        return self._data

class FakeDB:
    def __init__(self, query_data=None, will_raise_integrity=False):
        self.query_data = query_data or []
        self.added = []
        self.deleted = []
        self.committed = False
        self.will_raise_integrity = will_raise_integrity
    
    def query(self, model):
        return FakeQuery(self.query_data)
    
    def add(self, obj):
        if getattr(obj, 'verified', None) is None:
            obj.verified = False
        if getattr(obj, 'id', None) is None:
            obj.id = len(self.added) + 1
        self.added.append(obj)
        
    def delete(self, obj):
        self.deleted.append(obj)
        
    def commit(self):
        if self.will_raise_integrity:
            raise IntegrityError("mock", "mock", "mock")
        self.committed = True
        
    def refresh(self, obj):
        pass

    def rollback(self):
        pass

@pytest.fixture
def fake_db():
    db = FakeDB()
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    yield db
    app.dependency_overrides = {}

@pytest.fixture
def client():
    return TestClient(app)

def test_api_list_drivers(client, fake_db):
    fake_db.query_data = [
        F1Driver(id=1, season=2026, surname="VERSTAPPEN", number=1, team_name="Red Bull", team_colour="#000000", verified=False)
    ]
    resp = client.get("/api/f1-drivers?season=2026")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["surname"] == "VERSTAPPEN"

def test_api_create_driver(client, fake_db):
    payload = {
        "season": 2027,
        "surname": "SMITH",
        "number": 99,
        "team_name": "Test Team",
        "team_colour": "#AABBCC"
    }
    resp = client.post("/api/f1-drivers", json=payload)
    assert resp.status_code == 200
    assert resp.json()["surname"] == "SMITH"
    assert len(fake_db.added) == 1
    assert fake_db.added[0].surname == "SMITH"
    assert fake_db.committed is True

def test_api_create_driver_conflict(client):
    fake_db = FakeDB(will_raise_integrity=True)
    app.dependency_overrides[get_db] = lambda: fake_db
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    
    payload = {
        "season": 2027,
        "surname": "SMITH",
        "number": 99,
        "team_name": "Test Team",
        "team_colour": "#AABBCC"
    }
    resp = client.post("/api/f1-drivers", json=payload)
    assert resp.status_code == 409
    
    app.dependency_overrides = {}

def test_api_create_driver_validation(client, fake_db):
    payload = {
        "season": 2027,
        "surname": "JONES",
        "number": 99,
        "team_name": "Test Team",
        "team_colour": "red" # Invalid color
    }
    resp = client.post("/api/f1-drivers", json=payload)
    assert resp.status_code == 422

def test_api_upload_logo(client, fake_db, tmp_path):
    driver = F1Driver(id=1, season=2028, surname="DOE", number=8, team_name="Test", team_colour="#000000", verified=False)
    fake_db.query_data = [driver]
    
    from app.config import get_settings
    orig_settings = get_settings()
    
    class FakeSettings:
        def __getattr__(self, item):
            if item == "storage_base_path":
                return str(tmp_path)
            return getattr(orig_settings, item)
            
    from app.main import app
    from app.api.f1_drivers import get_settings as _get_settings
    app.dependency_overrides[_get_settings] = lambda: FakeSettings()
    
    import app.api.f1_drivers
    app.api.f1_drivers.get_settings = lambda: FakeSettings()

    # test invalid file type
    resp = client.post(
        "/api/f1-drivers/1/logo",
        files={"file": ("test.txt", b"not a png")}
    )
    assert resp.status_code == 400
    assert "Invalid file type" in resp.json()["detail"]

    # test valid png
    magic_png = b'\x89PNG\r\n\x1a\n' + b'rest of content'
    resp = client.post(
        "/api/f1-drivers/1/logo",
        files={"file": ("test.png", magic_png)}
    )
    assert resp.status_code == 200
    assert resp.json()["team_logo_path"] == f"team_logos/1_2028.png"
    assert driver.team_logo_path == "team_logos/1_2028.png"

