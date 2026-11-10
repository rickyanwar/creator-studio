"""Published delivery snapshot feeding contracts."""

from datetime import datetime, timedelta, timezone
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

from app.models.fb_group_share import FbShareBrandSettings, FbShareGroupJob, FbShareItem
from app.schemas.fb_group_share import merge_settings
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
        for model in (TargetFanpage, FbShareBrandSettings, SocialDestination, PublishJob, PublishDelivery, FbShareItem,
                      RadarStoryDecision, FbShareGroupJob):
            model.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        db.add_all([TargetFanpage(id=1, name="Brand", timezone="UTC"), TargetFanpage(id=2, name="Other")])
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


def brand(db, *, niches=None, mode="ig", per_day=10, source="off"):
    fanpage = db.get(TargetFanpage, 1)
    set_brand_share(db, fanpage.id, True, {"niches": ["f1"] if niches is None else niches,
                                          "sources": {mode: {"source": source, "follow_per_day": per_day}}})
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
def test_mapping_snapshots_expiry_and_rerun(db_session, content_type, mode):
    db = db_session
    fanpage = brand(db, mode=mode)
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
    # NOW is 12:00 UTC, so the brand's local day (UTC) starts 12 h earlier.
    delivery(db, published_at=NOW - timedelta(hours=12, seconds=1))
    delivery(db, state="scheduled")
    delivery(db, published_at=None)
    delivery(db, destination_id=3)
    delivery(db, destination_id=4)
    delivery(db, media_snapshot=[])
    delivery(db, media_snapshot=[{}, None, 12, " "])
    delivery(db, media_kind=None)
    boundary = delivery(db, published_at=NOW - timedelta(hours=12))
    assert collect_published_items(db, fanpage, NOW) == 1
    assert db.query(FbShareItem).one().source_ref == str(boundary.publish_job_id)


@pytest.mark.parametrize("source", ["off", "follow", "groups_only", "both"])
def test_zero_follow_per_day_not_fed_whatever_the_old_source(db_session, source):
    fanpage = brand(db_session, per_day=0, source=source)
    delivery(db_session)
    assert collect_published_items(db_session, fanpage, NOW) == 0


@pytest.mark.parametrize("source", ["off", "groups_only"])
def test_old_source_value_is_ignored(db_session, source):
    fanpage = brand(db_session, per_day=1, source=source)
    delivery(db_session)
    assert collect_published_items(db_session, fanpage, NOW) == 1


@pytest.mark.parametrize("mode,per_day,expected", [
    ("viral_radar", 1, 1), ("viral_radar", 0, 0), ("ig", 1, 0),
])
def test_radar_link_overrides_ig_source(db_session, mode, per_day, expected):
    db = db_session
    fanpage = brand(db, mode=mode, per_day=per_day)
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


def test_merge_settings_fills_follow_per_day_for_old_json():
    merged = merge_settings({"sources": {"ig": {"source": "follow", "groups_only_per_day": 2}}})
    assert merged["sources"]["ig"] == {"source": "follow", "groups_only_per_day": 2, "follow_per_day": 0}
    assert all(source["follow_per_day"] == 0 for source in merged["sources"].values())


def test_cap_takes_newest_first_and_counts_across_runs(db_session):
    db = db_session
    fanpage = brand(db, per_day=2)
    old, newest, middle = (delivery(db, published_at=NOW - timedelta(hours=hours)) for hours in (5, 1, 3))
    assert collect_published_items(db, fanpage, NOW) == 2
    refs = {item.source_ref for item in db.query(FbShareItem)}
    assert refs == {str(newest.publish_job_id), str(middle.publish_job_id)}
    later = delivery(db, published_at=NOW)
    assert collect_published_items(db, fanpage, NOW + timedelta(minutes=5)) == 0
    assert str(old.publish_job_id) not in {item.source_ref for item in db.query(FbShareItem)}
    assert str(later.publish_job_id) not in {item.source_ref for item in db.query(FbShareItem)}


def test_cap_is_per_mode(db_session):
    db = db_session
    fanpage = db.get(TargetFanpage, 1)
    set_brand_share(db, 1, True, {"niches": ["f1"], "sources": {
        "ig": {"follow_per_day": 1}, "news": {"follow_per_day": 2}}})
    db.commit()
    for _ in range(3):
        delivery(db, content_type=ContentType.ig_repost)
        delivery(db, content_type=ContentType.news_content)
    delivery(db, content_type=ContentType.discussion)
    assert collect_published_items(db, fanpage, NOW) == 3
    kinds = [db.get(PublishJob, int(item.source_ref)).content_type for item in db.query(FbShareItem)]
    assert sorted(kind.value for kind in kinds) == ["ig_repost", "news_content", "news_content"]


def test_items_from_before_local_midnight_do_not_count(db_session):
    db = db_session
    fanpage = brand(db, per_day=1)
    yesterday = delivery(db, published_at=NOW - timedelta(hours=20))
    assert collect_published_items(db, fanpage, NOW - timedelta(hours=13)) == 1
    assert db.query(FbShareItem).one().source_ref == str(yesterday.publish_job_id)
    delivery(db, published_at=NOW)
    assert collect_published_items(db, fanpage, NOW) == 1
    assert db.query(FbShareItem).count() == 2


@pytest.mark.parametrize("zone,expected", [("UTC", 0), ("America/New_York", 1), ("Not/AZone", 0), ("", 0)])
def test_brand_local_day_boundary(db_session, zone, expected):
    db = db_session
    fanpage = brand(db, per_day=1)
    fanpage.timezone = zone
    db.commit()
    # Collected at 03:00 UTC on Nov 8 = 22:00 Nov 7 in New York (a different local day than 12:00 UTC).
    delivery(db, published_at=NOW - timedelta(hours=10))
    assert collect_published_items(db, fanpage, NOW - timedelta(hours=9)) == 1
    delivery(db, published_at=NOW)
    assert collect_published_items(db, fanpage, NOW) == expected


def test_yesterday_evening_delivery_not_collected_after_midnight(db_session):
    db = db_session
    fanpage = brand(db)
    # 22:00 UTC on Nov 7, collected at 12:00 UTC on Nov 8: inside 48 h but a previous local day.
    delivery(db, published_at=NOW - timedelta(hours=14))
    assert collect_published_items(db, fanpage, NOW) == 0
    assert db.query(FbShareItem).count() == 0


@pytest.mark.parametrize("zone,expected", [("UTC", 1), ("America/New_York", 0)])
def test_published_today_uses_brand_local_day(db_session, zone, expected):
    db = db_session
    fanpage = brand(db)
    fanpage.timezone = zone
    db.commit()
    # 03:00 UTC on Nov 8 is today in UTC but 22:00 on Nov 7 (yesterday) in New York.
    delivery(db, published_at=NOW - timedelta(hours=9))
    assert collect_published_items(db, fanpage, NOW) == expected


def test_aware_now_is_accepted(db_session):
    db = db_session
    fanpage = brand(db)
    delivery(db, published_at=NOW - timedelta(hours=1))
    assert collect_published_items(db, fanpage, NOW.replace(tzinfo=timezone.utc)) == 1


def test_group_job_items_do_not_use_the_follow_quota(db_session):
    db = db_session
    fanpage = brand(db, per_day=1)
    group_job = PublishJob(fanpage_id=1, content_type=ContentType.ig_repost, created_at=NOW)
    db.add(group_job)
    db.flush()
    db.add(FbShareGroupJob(fanpage_id=1, mode="ig", publish_job_id=group_job.id, handed_at=NOW))
    db.add(FbShareItem(fanpage_id=1, niche="f1", source_kind="publish_job", source_ref=str(group_job.id),
                       media_kind="photo", media=["g.jpg"], status="new", created_at=NOW))
    db.commit()
    page_post = delivery(db)
    assert collect_published_items(db, fanpage, NOW) == 1
    assert db.query(FbShareItem).filter_by(source_ref=str(page_post.publish_job_id)).count() == 1


def test_skipped_media_does_not_use_the_quota(db_session):
    db = db_session
    fanpage = brand(db, per_day=1)
    delivery(db, media_snapshot=[], published_at=NOW)
    usable = delivery(db, published_at=NOW - timedelta(hours=1))
    assert collect_published_items(db, fanpage, NOW) == 1
    assert db.query(FbShareItem).one().source_ref == str(usable.publish_job_id)
