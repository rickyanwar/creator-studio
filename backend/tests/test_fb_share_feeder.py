"""Published delivery snapshot feeding contracts."""

from datetime import datetime, timedelta
import io
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import pytest
from PIL import Image
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import DefaultClause

from app.models.fb_group_share import FbShareBrandSettings, FbShareItem
from app.services.fb_share.brand_settings import set_brand_share
from app.models.publish_jobs import ContentType, PublishJob
from app.models.radar import RadarStoryDecision
from app.models.social_publishing import PublishDelivery, SocialDestination
from app.models.target_fanpages import TargetFanpage
from app.services.fb_share.feeder import collect_published_items
from app.services.fb_share.feeder import load_image_bytes as _real_load_image_bytes
from app.services.fb_share import feeder
from app.services.radar_clustering import compute_phash


NOW = datetime(2026, 11, 8, 12)


@pytest.fixture
def image_bytes():
    output = io.BytesIO()
    Image.new("RGB", (32, 32), "red").save(output, format="PNG")
    return output.getvalue()


@pytest.fixture(autouse=True)
def offline_loader(monkeypatch, image_bytes):
    monkeypatch.setattr(feeder, "load_image_bytes", Mock(return_value=image_bytes), raising=False)
MODES = [
    (ContentType.ig_repost, "ig"), (ContentType.ig_recreate, "ig"),
    (ContentType.news_content, "news"), (ContentType.discussion, "discussion"),
    (ContentType.pinterest_content, "pinterest"),
    (ContentType.facebook_recreate, "fb_recreate"),
    (ContentType.youtube_clip, "yt_clips"), (ContentType.ai_video, "ai_video"),
]


@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://")
    column = SocialDestination.__table__.c.supported_media
    with patch.object(column, "server_default", DefaultClause(text("'[]'"))):
        for model in (TargetFanpage, FbShareBrandSettings, SocialDestination, PublishJob, PublishDelivery, FbShareItem, RadarStoryDecision):
            model.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([TargetFanpage(id=1, name="Brand"), TargetFanpage(id=2, name="Other")])
        db.flush()
        db.add_all([
            SocialDestination(id=1, content_project_id=1, kind="facebook_profile", name="Profile"),
            SocialDestination(id=2, content_project_id=1, kind="facebook_page", name="Page"),
            SocialDestination(id=3, content_project_id=2, kind="facebook_page", name="Other"),
            SocialDestination(id=4, content_project_id=1, kind="instagram", name="IG"),
        ])
        db.commit()
        yield db
    engine.dispose()


def brand(db, *, niches=None, mode="ig", source="follow"):
    fanpage = db.get(TargetFanpage, 1)
    set_brand_share(db, fanpage.id, True, {"niches": ["f1"] if niches is None else niches,
                                          "sources": {mode: {"source": source}}})
    db.commit()
    return fanpage


def delivery(db, *, content_type=ContentType.ig_repost, destination_id=1, **values):
    job = PublishJob(fanpage_id=1, content_type=content_type, created_at=NOW - timedelta(days=10))
    db.add(job)
    db.flush()
    fields = dict(publish_job_id=job.id, destination_id=destination_id, state="published",
                  published_at=NOW, media_kind="image", media_snapshot=["photo.jpg"],
                  caption_snapshot="Max wins #F1 #Racing")
    fields.update(values)
    row = PublishDelivery(**fields)
    db.add(row)
    db.commit()
    return row


@pytest.mark.parametrize("content_type,mode", MODES)
@pytest.mark.parametrize("source", ["follow", "both"])
def test_mapping_snapshots_expiry_and_rerun(db_session, content_type, mode, source):
    db = db_session
    fanpage = brand(db, mode=mode, source=source)
    original = delivery(db, content_type=content_type)
    duplicate = PublishDelivery(publish_job_id=original.publish_job_id, destination_id=2,
                                state="published", published_at=NOW, media_kind="image",
                                media_snapshot=["other.jpg"])
    db.add(duplicate)
    db.commit()
    with patch.object(db, "commit", wraps=db.commit) as commit:
        assert collect_published_items(db, fanpage, NOW) == 1
        assert commit.call_count == 1
    item = db.query(FbShareItem).one()
    assert (item.niche, item.source_kind, item.source_ref) == ("f1", "publish_job", str(original.publish_job_id))
    assert (item.caption, item.hashtags, item.media, item.media_kind, item.status) == (
        "Max wins", "#F1 #Racing", ["photo.jpg"], "photo", "new")
    assert item.created_at == NOW
    assert item.expires_at == NOW + timedelta(hours=12 if mode == "news" else 72)
    assert collect_published_items(db, fanpage, NOW) == 0
    assert original.caption_snapshot == "Max wins #F1 #Racing"
    assert db.query(PublishDelivery).count() == 2


def test_window_state_destination_and_empty_media_filters(db_session):
    db = db_session
    fanpage = brand(db)
    delivery(db, published_at=NOW - timedelta(hours=48, seconds=1))
    delivery(db, state="scheduled")
    delivery(db, published_at=None)
    delivery(db, destination_id=3)
    delivery(db, destination_id=4)
    delivery(db, media_snapshot=[])
    delivery(db, media_snapshot=[{}, None, 12, " "])
    delivery(db, media_kind=None)
    boundary = delivery(db, published_at=NOW - timedelta(hours=48))
    assert collect_published_items(db, fanpage, NOW) == 1
    assert db.query(FbShareItem).one().source_ref == str(boundary.publish_job_id)


@pytest.mark.parametrize("source", ["off", "groups_only"])
def test_disabled_sources_not_fed(db_session, source):
    fanpage = brand(db_session, source=source)
    delivery(db_session)
    assert collect_published_items(db_session, fanpage, NOW) == 0


@pytest.mark.parametrize("mode,source,expected", [
    ("viral_radar", "follow", 1), ("viral_radar", "both", 1),
    ("viral_radar", "off", 0), ("viral_radar", "groups_only", 0),
    ("ig", "follow", 0), ("ig", "both", 0),
])
def test_radar_link_overrides_ig_source(db_session, mode, source, expected):
    db = db_session
    fanpage = brand(db, mode=mode, source=source)
    row = delivery(db, content_type=ContentType.ig_recreate)
    db.add(RadarStoryDecision(story_id=1, fanpage_id=1, rule="test", status="published",
                              publish_job_id=row.publish_job_id))
    db.commit()
    assert collect_published_items(db, fanpage, NOW) == expected
    assert db.query(FbShareItem).count() == expected


def test_unlinked_ig_recreate_does_not_use_radar_source(db_session):
    fanpage = brand(db_session, mode="viral_radar")
    delivery(db_session, content_type=ContentType.ig_recreate)
    assert collect_published_items(db_session, fanpage, NOW) == 0


@pytest.mark.parametrize("niches,expected", [([], None), (["f1", "motogp"], "")])
def test_niche_rules(db_session, niches, expected):
    fanpage = brand(db_session, niches=niches)
    delivery(db_session)
    assert collect_published_items(db_session, fanpage, NOW) == (0 if expected is None else 1)
    item = db_session.query(FbShareItem).first()
    assert (item.niche if item else None) == expected


@pytest.mark.parametrize("media_kind,snapshot,expected", [
    ("image", {"url": "url.jpg", "path": "local.jpg"}, ["url.jpg"]),
    ("album", [{"url": "a.jpg"}, {"path": "b.jpg"}, "c.jpg", {}], ["a.jpg", "b.jpg", "c.jpg"]),
    ("video", [{"url": "clip.mp4"}], ["clip.mp4"]),
])
def test_media_shapes(db_session, media_kind, snapshot, expected):
    fanpage = brand(db_session)
    delivery(db_session, media_kind=media_kind, media_snapshot=snapshot)
    assert collect_published_items(db_session, fanpage, NOW) == 1
    item = db_session.query(FbShareItem).one()
    assert item.media == expected
    assert item.media_kind == ("photo" if media_kind == "image" else media_kind)


@pytest.mark.parametrize("caption,body,tags", [
    ("#F1", "", "#F1"), ("Text #F1 in context", "Text #F1 in context", ""),
    ("Text\n#MotoGP  #Balapan_2026 \n", "Text", "#MotoGP #Balapan_2026"),
    (None, "", ""),
])
def test_trailing_hashtags(db_session, caption, body, tags):
    fanpage = brand(db_session)
    delivery(db_session, caption_snapshot=caption)
    assert collect_published_items(db_session, fanpage, NOW) == 1
    item = db_session.query(FbShareItem).one()
    assert (item.caption, item.hashtags) == (body, tags)


@pytest.mark.parametrize("kind", ["image", "album", "video"])
def test_first_media_phash(db_session, image_bytes, kind):
    fanpage = brand(db_session)
    delivery(db_session, media_kind=kind, media_snapshot=["first.jpg", "second.jpg"])
    loader = Mock(return_value=image_bytes)
    assert collect_published_items(db_session, fanpage, NOW, loader=loader) == 1
    item = db_session.query(FbShareItem).one()
    if kind == "video":
        assert item.phash is None
        loader.assert_not_called()
    else:
        assert item.phash == compute_phash(image_bytes)
        loader.assert_called_once_with("first.jpg")


@pytest.mark.parametrize("kind", ["image", "album"])
@pytest.mark.parametrize("failure", [OSError("unavailable"), b"invalid image"])
def test_unhashable_photo_is_skipped(db_session, caplog, kind, failure):
    fanpage = brand(db_session)
    delivery(db_session, media_kind=kind)
    loader = Mock(side_effect=failure) if isinstance(failure, Exception) else Mock(return_value=failure)
    assert collect_published_items(db_session, fanpage, NOW, loader=loader) == 0
    assert db_session.query(FbShareItem).count() == 0
    assert "phash" in caplog.text and "WARNING" in caplog.text


@pytest.mark.parametrize("ref", ["absolute", "photo.png", "media/photo.png", "/media/photo.png"])
def test_local_image_loading(monkeypatch, tmp_path, image_bytes, ref):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes)
    monkeypatch.setattr(feeder, "get_settings", lambda: SimpleNamespace(storage_base_path=str(tmp_path)))
    monkeypatch.setattr(feeder.httpx, "stream", Mock(side_effect=AssertionError("Unexpected HTTP")))
    assert _real_load_image_bytes(str(path) if ref == "absolute" else ref) == image_bytes


@pytest.mark.parametrize("status,data", [(200, b"image"), (200, b"oversized"), (404, b"missing")])
def test_http_image_loading(monkeypatch, status, data):
    monkeypatch.setattr(feeder, "MAX_IMAGE_BYTES", 5)
    response = httpx.Response(status, content=data, request=httpx.Request("GET", "https://example.test/x"))
    stream = Mock()
    stream.return_value.__enter__ = Mock(return_value=response)
    stream.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr(feeder.httpx, "stream", stream)
    if status == 200 and len(data) <= 5:
        assert _real_load_image_bytes("https://example.test/x") == data
    else:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            _real_load_image_bytes("https://example.test/x")
    stream.assert_called_once_with("GET", "https://example.test/x", timeout=15.0, follow_redirects=True)
