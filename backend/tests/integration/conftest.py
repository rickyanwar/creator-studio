import os
import pytest
from unittest.mock import MagicMock
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL")

if not TEST_DATABASE_URL:
    pytest.skip("TEST_DATABASE_URL not set, skipping integration tests", allow_module_level=True)

@pytest.fixture(scope="session")
def engine():
    engine = create_engine(TEST_DATABASE_URL)
    from app.database import Base
    # The database is assumed to be at alembic head, no need to create_all
    yield engine
    engine.dispose()

@pytest.fixture
def db_session(engine, monkeypatch):
    connection = engine.connect()
    transaction = connection.begin()
    
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    
    # Make close a no-op so tasks don't close our shared test session
    original_close = session.close
    session.close = lambda: None
    
    # Patch SessionLocal in tasks
    def get_session():
        return session
        
    monkeypatch.setattr("app.tasks.radar_dispatch.SessionLocal", get_session)
    monkeypatch.setattr("app.tasks.ig_recreate.SessionLocal", get_session)
    monkeypatch.setattr("app.tasks.fan_out.SessionLocal", get_session)
    
    yield session
    
    session.close = original_close
    session.close()
    transaction.rollback()
    connection.close()


class FakeRedis:
    def __init__(self):
        self.data = {}
    def set(self, k, v, nx=False, ex=None):
        if nx and k in self.data: return False
        self.data[k] = v
        return True
    def delete(self, k):
        self.data.pop(k, None)
    def eval(self, script, keys_len, key, arg):
        if self.data.get(key) == arg:
            self.data.pop(key, None)
            return 1
        return 0

@pytest.fixture(autouse=True)
def patch_celery_and_ai(monkeypatch):
    class FakeCall:
        def __init__(self):
            self.calls = []
        def delay(self, *args, **kwargs):
            self.calls.append(("delay", args, kwargs))
        def apply_async(self, args=None, kwargs=None, countdown=0):
            self.calls.append(("apply_async", args, kwargs, countdown))
            
    fanout = FakeCall()
    recreate = FakeCall()
    render = FakeCall()
    save_images = FakeCall()
    ai_gen_caption = FakeCall()
    
    fake_redis = FakeRedis()
    
    monkeypatch.setattr("app.tasks.fan_out.create_fanout_jobs", fanout)
    monkeypatch.setattr("app.tasks.ig_recreate.recreate_post_for_fanpage.apply_async", recreate.apply_async)
    monkeypatch.setattr("app.tasks.ig_recreate.render_ig_recreate.delay", render.delay)
    monkeypatch.setattr("app.tasks.ai_generator.generate_caption_for_job.apply_async", ai_gen_caption.apply_async)
    # mock the synchronously called save_post_images
    def mock_save_images(p, u):
        save_images.calls.append(("save_post_images", p, u))
        
    monkeypatch.setattr("app.tasks.image_saver.save_post_images", mock_save_images)
    
    # mock download_ig_image used in dispatch
    def mock_download_ig_image(url, dest):
        if dest is not None:
            open(dest, "wb").close() # create empty file
        return b"fake"
        
    monkeypatch.setattr("app.tasks.radar_dispatch.download_ig_image", mock_download_ig_image)
    monkeypatch.setattr("app.tasks.image_saver.download_ig_image", mock_download_ig_image)
    
    # mock redis
    monkeypatch.setattr("app.tasks.radar_dispatch._redis", lambda: fake_redis)
    monkeypatch.setattr("app.tasks.radar._redis", lambda: fake_redis)
    monkeypatch.setattr("app.tasks.radar_crawl._redis", lambda: fake_redis)
    
    # Fakes
    monkeypatch.setattr("app.services.ai_caption.generate_caption", lambda p: ("fake caption", "gemini"))
    from app.services.ig_content_classifier import PostAnalysis
    monkeypatch.setattr("app.services.ig_content_classifier.analyze_ig_post", lambda b, c, niche=None, allow_team_radio=False: PostAnalysis(type="news", text="fake headline", speaker="", main_subject="", secondary_kind="none", secondary="", inset_context="", radio_lines=[], moment_summary="fake headline", inset_query="", inset_contexts=(), weather="dry"))
    monkeypatch.setattr("app.services.design_images.source_news_main", lambda db, title, niche, exclude_paths=None: ("data:image/jpeg;base64,123", "fake/path.jpg"))
    monkeypatch.setattr("httpx.post", lambda *a, **kw: MagicMock(content=b"fake png", raise_for_status=lambda: None))
    
    # mock settings
    monkeypatch.setattr("app.tasks.radar_dispatch.app_settings.storage_base_path", "/tmp/media")
    monkeypatch.setattr("app.tasks.ig_recreate.settings.storage_base_path", "/tmp/media")
    
    return {
        "fanout": fanout,
        "recreate": recreate,
        "render": render,
        "save_images": save_images,
        "ai_gen_caption": ai_gen_caption
    }
