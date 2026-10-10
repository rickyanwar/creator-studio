import pytest
from fastapi.testclient import TestClient
from unittest.mock import MagicMock
from app.main import app
from app.api.deps import get_db, get_current_user

# Setup simple mock DB
class FakeQuery:
    def __init__(self, data=None):
        self._data = data or []
    def filter(self, *args):
        return self
    def options(self, *args):
        return self
    def filter_by(self, **kwargs):
        return self
    def limit(self, val):
        return self
    def order_by(self, *args):
        return self
    def first(self):
        return self._data[0] if self._data else None
    def all(self):
        return self._data
    def distinct(self):
        return self

class FakeDB:
    def __init__(self, query_data=None):
        self.query_data = query_data or []
        self.added = []
        self.deleted = []
        self.committed = False
    
    def query(self, model):
        # We can simulate returning some data
        return FakeQuery(self.query_data)
        
    def add(self, obj):
        obj.id = 1
        if getattr(obj, "is_active", None) is None:
            obj.is_active = True
        if getattr(obj, "leader_score", None) is None:
            obj.leader_score = 0.0
        self.added.append(obj)
        
    def delete(self, obj):
        self.deleted.append(obj)
        
    def commit(self):
        self.committed = True
        
    def refresh(self, obj):
        pass

def override_get_current_user():
    return {"id": 1, "username": "admin"}

@pytest.fixture
def client():
    app.dependency_overrides[get_current_user] = override_get_current_user
    yield TestClient(app)
    app.dependency_overrides.clear()

def test_radar_accounts_auth_required():
    # clear overrides
    app.dependency_overrides.clear()
    client = TestClient(app)
    res = client.get("/api/radar/accounts")
    assert res.status_code in (401, 403)

def test_radar_accounts_crud(client):
    # GET
    fake_db = FakeDB()
    app.dependency_overrides[get_db] = lambda: fake_db
    res = client.get("/api/radar/accounts")
    assert res.status_code == 200
    assert res.json() == []

    # POST (validation error: bad username)
    res = client.post("/api/radar/accounts", json={"niche": "f1", "ig_username": "invalid username!"})
    assert res.status_code == 422
    
    # POST (success)
    res = client.post("/api/radar/accounts", json={"niche": "f1", "ig_username": "@Valid.Name"})
    assert res.status_code == 201
    assert res.json()["ig_username"] == "valid.name"
    
    # POST (conflict duplicate)
    fake_db = FakeDB(query_data=[{"id": 1}]) # simulate existing
    app.dependency_overrides[get_db] = lambda: fake_db
    res = client.post("/api/radar/accounts", json={"niche": "f1", "ig_username": "valid.name"})
    assert res.status_code == 409
    
    # PATCH
    class FakeAcc:
        id = 1
        niche = "f1"
        ig_username = "valid.name"
        is_active = True
        leader_score = 0.0
        last_checked_at = None
        last_post_seen_at = None
        avg_scrape_seconds = None
        last_error = None
    
    fake_db = FakeDB(query_data=[FakeAcc()])
    app.dependency_overrides[get_db] = lambda: fake_db
    res = client.patch("/api/radar/accounts/1", json={"is_active": False})
    # Will hit conflict check query because we mock it all. Wait, if body.niche is None, it won't check conflict.
    assert res.status_code == 200
    assert fake_db.committed is True
    
    # DELETE
    res = client.delete("/api/radar/accounts/1")
    assert res.status_code == 204
    assert fake_db.deleted

    def test_radar_niches(client):
        class FakeTargetFP:
            pass
        fake_db = FakeDB(query_data=[(["f1", "ufc"],)])
        app.dependency_overrides[get_db] = lambda: fake_db
        res = client.get("/api/radar/niches")
        assert res.status_code == 200

def test_fanpage_trigger(client):
    class FakeLink:
        ig_source_id = 1
        trigger = "every_post"
    fake_db = FakeDB(query_data=[FakeLink()])
    app.dependency_overrides[get_db] = lambda: fake_db
    
    # Validation error
    res = client.put("/fanpages/1/sources/1/trigger", json={"trigger": "bad_trigger"})
    assert res.status_code == 422
    
    # Success
    res = client.put("/fanpages/1/sources/1/trigger", json={"trigger": "viral_only"})
    assert res.status_code == 200
    assert res.json()["trigger"] == "viral_only"
    
    # 404
    fake_db = FakeDB(query_data=[])
    app.dependency_overrides[get_db] = lambda: fake_db
    res = client.put("/fanpages/1/sources/1/trigger", json={"trigger": "viral_only"})
    assert res.status_code == 404

def test_fanpage_radar_fields_validation(client):
    from app.schemas.fanpage import FanpageUpdate
    # Valid
    fp = FanpageUpdate(visual_engine="auto", radar_min_likes=1000, radar_niches=[" f1 ", "f1", "", "a"*65, "ufc"])
    assert fp.visual_engine == "auto"
    assert fp.radar_niches == ["f1", "ufc"] # deduplicated, trimmed, empty/long dropped
    
    # Invalid engine
    res = client.put("/fanpages/1", json={"visual_engine": "invalid"})
    # it might return 401/403 or 404/422 based on the actual endpoint, but the validation works
    # We can just test Pydantic directly for fast validation:
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError) as exc:
        FanpageUpdate(visual_engine="invalid")
    assert "pattern" in str(exc.value)

    with pytest.raises(ValidationError):
        FanpageUpdate(radar_min_likes=0)
    with pytest.raises(ValidationError):
        FanpageUpdate(radar_confirm_ratio=25.0)
    with pytest.raises(ValidationError):
        FanpageUpdate(radar_fast_window_min=4)
    with pytest.raises(ValidationError):
        FanpageUpdate(radar_shelf_news_h=1000)
    with pytest.raises(ValidationError):
        FanpageUpdate(radar_daily_max=101)

def test_settings_radar_fields(client):
    class FakeSettings:
        id = 1
        crawl_interval_minutes = 30
        max_post_age_days = 2
        ai_provider_primary = "gemini"
        ai_provider_fallback = "groq"
        storage_base_url = ""
        storage_base_path = ""
        ai_fallback_after_failures = 3
        ai_fallback_reset_after_minutes = 60
        ai_gemini_api_key_encrypted = None
        ai_groq_api_key_encrypted = None
        repliz_access_key_encrypted = None
        repliz_secret_key_encrypted = None
        telegram_bot_token_encrypted = None
        telegram_chat_id = None
        scraper_mode = "auto"
        scraper_proxies = ""
        scraper_relays = ""
        gallery_scraping_paused = False
        gallery_ai_filter_last_criteria = None
        nine_router_base_url = None
        nine_router_model = None
        nine_router_discussion_model = None
        nine_router_api_key_encrypted = None
        youtube_cookies_encrypted = None
        youtube_proxy = None
        youtube_blocked_until = None
        youtube_last_error = None
        
        radar_sleep_start_wib = 0
        radar_sleep_end_wib = 6
        radar_very_hot_interval_min = 8
        radar_hot_interval_min = 12
        radar_cold_interval_min = 50
        radar_hot_window_h = 2
        radar_track_max_age_h = 48
        radar_story_sharing = "shared"
        radar_share_max = 0
        radar_news_stagger_max_min = 10

    fake_db = FakeDB(query_data=[FakeSettings()])
    app.dependency_overrides[get_db] = lambda: fake_db
    
    # GET settings
    res = client.get("/settings")
    assert res.status_code == 200
    assert res.json()["radar_story_sharing"] == "shared"
    
    # PUT settings
    res = client.put("/settings", json={"radar_news_stagger_max_min": 15})
    assert res.status_code == 200
    assert fake_db.committed is True

def test_radar_stories(client):
    from app.models.radar import RadarStory, RadarPost, RadarStoryDecision
    from app.models.target_fanpages import TargetFanpage
    from datetime import datetime, timezone
    
    fp = TargetFanpage()
    fp.id = 1
    fp.name = "Test Fanpage"
    
    p = RadarPost()
    p.ig_username = "testuser"
    p.shortcode = "abcd"
    p.taken_at = datetime.now(timezone.utc)
    p.latest_like_count = 1000
    p.latest_comment_count = 10
    p.thumbnail_url = "http://example.com/thumb.jpg"
    
    d = RadarStoryDecision()
    d.id = 1
    d.fanpage_id = 1
    d.fanpage = fp
    d.rule = "fast"
    d.status = "created"
    d.shadow = False
    d.would_trigger_at = None
    d.reason = None
    d.publish_job_id = None
    d.preview_status = None
    d.preview_error = None
    
    s = RadarStory()
    s.id = 1
    s.niche = "f1"
    s.first_seen_at = datetime.now(timezone.utc)
    s.last_member_at = datetime.now(timezone.utc)
    s.member_count = 1
    s.distinct_accounts = 1
    s.heat_score = 1.5
    s.status = "watching"
    s.shelf_kind = "news"
    s.expires_at = datetime.now(timezone.utc)
    s.final_max_likes_24h = None
    s.posts = [p]
    s.decisions = [d]
    
    fake_db = FakeDB(query_data=[s])
    app.dependency_overrides[get_db] = lambda: fake_db
    
    res = client.get("/api/radar/stories?limit=10")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == 1
    assert len(data[0]["members"]) == 1
    assert data[0]["members"][0]["ig_username"] == "testuser"
    assert len(data[0]["decisions"]) == 1
    assert data[0]["decisions"][0]["fanpage_name"] == "Test Fanpage"

def test_radar_stories_bounds(client):
    app.dependency_overrides.clear()
    client = TestClient(app)
    # Auth missing
    assert client.get("/api/radar/stories").status_code in (401, 403)
    
    # Overrides back
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[get_db] = lambda: FakeDB()
    
    # limits
    assert client.get("/api/radar/stories?limit=0").status_code == 422
    assert client.get("/api/radar/stories?limit=201").status_code == 422
    assert client.get("/api/radar/stories?limit=100").status_code == 200

def test_shadow_report(client):
    app.dependency_overrides[get_db] = lambda: FakeDB()
    # bounds
    assert client.get("/api/radar/shadow-report?days=0").status_code == 422
    assert client.get("/api/radar/shadow-report?days=31").status_code == 422
    
    # proper call
    res = client.get("/api/radar/shadow-report?days=7")
    assert res.status_code == 200
    data = res.json()
    assert "overall" in data
    assert "fanpages" in data
