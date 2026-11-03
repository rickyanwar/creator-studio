"""Offline regression checks for Mode 6 source cards and original retention."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services import facebook_photo_source as service


@pytest.mark.parametrize("raw, expected", [
    ("https://www.facebook.com/racing", "racing/photos"),
    ("racing", "racing/photos"),
    ("https://www.facebook.com/profile.php?id=123&junk=1", "profile.php?id=123&sk=photos"),
    ("https://www.facebook.com/people/Racing-Page/123/photos", "people/Racing-Page/123/?sk=photos"),
    ("https://m.facebook.com/racing", "racing/photos"),
    ("https://web.facebook.com/racing", "racing/photos"),
])
def test_photos_url(raw, expected):
    assert service._photos_url(raw) == f"https://www.facebook.com/{expected}"


@pytest.mark.parametrize("raw", ["", "https://example.com/racing", "https://www.facebook.com/", "bad input", "https://www.facebook.com/profile.php?id=no", "https://www.facebook.com/people/Name/no"])
def test_bad_photos_url(raw):
    with pytest.raises(ValueError):
        service._photos_url(raw)


def test_cache_busting_query_separator(monkeypatch):
    fetch = MagicMock(return_value="empty")
    monkeypatch.setattr("app.services.image_downloader._9router_fetch_markdown", fetch)
    service._fetch_grid_markdown("https://m.facebook.com/profile.php?id=123")
    assert fetch.call_count == 3
    assert all("?id=123&sk=photos&_cb=" in call.args[0] for call in fetch.call_args_list)


@pytest.mark.parametrize("marker", [
    "[GPFans Global's Photos](https://www.facebook.com/gpfansglobal/photos_by)",
    "[Photos](http://www.facebook.com/gpfansglobal/photos_by)",
    "[Photos](https://www.facebook.com/profile.php?id=123&sk=photos_by)",
    "[Photos](https://www.facebook.com/people/Racing/123/?sk=photos_by)",
    "",
])
def test_candidates_only_include_grid_photos(monkeypatch, marker):
    before_grid = "[![Image 2](https://cdn/header.jpg)](https://www.facebook.com/photo/?fbid=104)" if marker else ""
    markdown = f"""
{before_grid}
[![Image 1](https://cdn/profile.jpg?stp=cp0_dst-jpg_s320x320)](https://www.facebook.com/photo/?fbid=101)
[![Image 3](https://cdn/cover.jpg?ctp=s820x360)](https://www.facebook.com/photo/?fbid=1387479636715902)
[![Image 4](https://cdn/cover-large.jpg?cstp=mx820x360)](https://www.facebook.com/photo/?fbid=102)
[![Image 5](https://cdn/profile-fb50.jpg)](https://www.facebook.com/photo/?fbid=103)
{marker}
[![Image 6: First post](https://cdn/first.jpg?cstp=mx1080x1080&ctp=s206x206)](https://www.facebook.com/photo/?fbid=201)
[![Image 7: Second post](https://cdn/second.jpg?cstp=mx1080x1080&ctp=s206x206)](https://www.facebook.com/photo/?fbid=202)
[![Image 8: Third post](https://cdn/third.jpg?cstp=mx1080x1080&ctp=s206x206)](https://www.facebook.com/photo/?fbid=203)
"""
    monkeypatch.setattr(service, "_fetch_grid_markdown", lambda page_url: markdown)
    candidates = service.fetch_photo_candidates("gpfansglobal", limit=3)
    assert [candidate.fbid for candidate in candidates] == ["201", "202", "203"]
    assert all("ctp=s206x206" not in candidate.image_url for candidate in candidates)


def test_per_fanpage_and_legacy_dedup():
    db = MagicMock()
    db.query.return_value.filter.return_value.all.return_value = [
        ("facebook_photo:1:123",), ("facebook_photo:456",),
        ("facebook_photo:2:789",), (None,), ("unrelated",),
    ]
    assert service._seen_fbids(db, 1) == {"123", "456"}
    assert service._seen_fbids(db, 2) == {"789", "456"}


def test_record_seen_scopes_unique_url_per_fanpage():
    candidate = service.FacebookPhotoCandidate("123", "https://cdn/photo.jpg", "")
    item = service._DownloadedPhoto(candidate.image_url, "/tmp/not-created.jpg", "x.jpg", 400, 400)
    a = service._record_seen(MagicMock(), 1, candidate, item)
    b = service._record_seen(MagicMock(), 2, candidate, item)
    assert a.keyword == "facebook_photo:1:123"
    assert b.keyword == "facebook_photo:2:123"
    assert a.source_image_url != b.source_image_url


@pytest.mark.parametrize("outcome, dimensions", [
    ("accepted", (1080, 1080)), ("accepted", (1600, 1000)), ("accepted", (1000, 1600)),
    ("banner", (820, 360)), ("banner", (360, 820)),
    ("banner", (1601, 1000)), ("banner", (1000, 1601)),
    ("off_topic", (400, 400)), ("classify_error", (400, 400)),
    ("rewrite_error", (400, 400)), ("incomplete", (400, 400)),
])
def test_download_retention(tmp_path, monkeypatch, outcome, dimensions):
    downloaded = tmp_path / "download.jpg"
    downloaded.write_bytes(b"original photo")
    candidate = service.FacebookPhotoCandidate("123", "https://cdn/photo.jpg", "")
    item = service._DownloadedPhoto(candidate.image_url, str(downloaded), downloaded.name, *dimensions)
    fp = SimpleNamespace(id=1, name="Racing", mode2_gallery_niches=[], facebook_photo_topic_filter=None)
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(storage_base_path=str(tmp_path)))
    monkeypatch.setattr(service, "_seen_fbids", lambda db, fanpage_id: set())
    monkeypatch.setattr(service, "_download_photo", lambda *args: item)
    record_seen = MagicMock(return_value=SimpleNamespace(id=99))
    monkeypatch.setattr(service, "_record_seen", record_seen)
    classifier = MagicMock(return_value={
        "type": "quote" if outcome == "incomplete" else "news",
        "headline": "Race result", "quote": "He won but", "speaker": "Driver",
        "on_topic": outcome != "off_topic",
    })
    if outcome == "classify_error":
        classifier.side_effect = RuntimeError("offline failure")
    monkeypatch.setattr("app.services.facebook_content_classifier.classify_facebook_photo", classifier)
    localize = MagicMock(return_value="Race result")
    if outcome == "rewrite_error":
        localize.side_effect = RuntimeError("offline failure")
    monkeypatch.setattr(service, "_localize_title", localize)
    db = MagicMock()
    idea = service.build_idea_from_candidate(db, fp, candidate, source_id=7)
    assert not downloaded.exists()
    retained = tmp_path / "facebook_photos" / "1" / "123.jpg"
    if outcome == "accepted":
        assert retained.read_bytes() == b"original photo"
        assert idea.image_local_path == str(retained)
        assert idea.source_id == 7
    else:
        assert idea is None
        assert not retained.exists()
    if outcome == "banner":
        record_seen.assert_called_once_with(db, fp.id, candidate, item)
        classifier.assert_not_called()


@pytest.fixture
def source_db():
    from app.models.facebook_photo_sources import FacebookPhotoSource
    from app.models.facebook_photo_ideas import FacebookPhotoIdea

    engine = create_engine("sqlite://")
    FacebookPhotoSource.__table__.create(engine)
    FacebookPhotoIdea.__table__.create(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


@pytest.mark.parametrize("start_pending, failure", [(0, False), (4, False), (5, False), (0, True)])
def test_fair_source_turns(source_db, monkeypatch, start_pending, failure):
    from app.models.facebook_photo_sources import FacebookPhotoSource
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.tasks.facebook_photo import _topup_queue

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sources = [FacebookPhotoSource(fanpage_id=1, page_url=str(i), last_fetched_at=stamp)
               for i, stamp in enumerate([now, now - timedelta(days=2), None, now - timedelta(days=1)])]
    source_db.add_all(sources)
    for i in range(start_pending):
        source_db.add(FacebookPhotoIdea(fanpage_id=1, gallery_image_id=i + 1, category="news", design_title="Title"))
    source_db.commit()
    fetch = MagicMock(return_value=[service.FacebookPhotoCandidate("123", "https://cdn/photo.jpg", "")] * 6 if start_pending == 4 else [])
    if failure:
        fetch.side_effect = ValueError("invalid page")
    monkeypatch.setattr(service, "fetch_photo_candidates", fetch)
    monkeypatch.setattr(service, "_seen_fbids", lambda *args: set())
    build = MagicMock(return_value=True)
    monkeypatch.setattr(service, "build_idea_from_candidate", build)
    created = _topup_queue(source_db, SimpleNamespace(id=1))
    expected = [] if start_pending == 5 else (["2"] if start_pending == 4 else ["2", "1", "3"])
    assert [call.args[0] for call in fetch.call_args_list] == expected
    assert created == (1 if start_pending == 4 else 0)
    for source in sources:
        if source.page_url in expected:
            assert source.last_fetched_at >= now
            assert source.times_used == (0 if failure else 1)
    if start_pending == 4:
        assert build.call_count == 1
        assert build.call_args.kwargs["source_id"] == sources[2].id


@pytest.mark.parametrize("idea_status, job_status, age, live_pending, recent_delivery, delete", [
    ("used", "published", 8, False, False, True),
    ("pending", "published", 8, False, False, False),
    ("unused", "published", 8, False, False, False),
    ("failed", "published", 8, False, False, False),
    ("used", "failed", 8, False, False, False),
    ("used", "published", 3, False, False, False),
    ("used", "published", 8, True, False, False),
    ("used", "published", 8, False, True, False),
])
def test_cleanup_original(source_db, tmp_path, monkeypatch, idea_status, job_status, age, live_pending, recent_delivery, delete):
    from app.models.facebook_photo_ideas import FacebookPhotoIdea
    from app.models.publish_jobs import PublishJob
    from app.tasks import cleanup

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    photo = tmp_path / "original.jpg"
    photo.write_bytes(b"original")
    idea = FacebookPhotoIdea(fanpage_id=1, gallery_image_id=99, category="news", design_title="Title", status=idea_status, image_local_path=str(photo))
    source_db.add(idea)
    source_db.commit()
    job = SimpleNamespace(id=10, status=job_status, published_at=now - timedelta(days=age), scheduled_for=None, deliveries=[])
    if recent_delivery:
        job.deliveries = [SimpleNamespace(scheduled_for=now - timedelta(days=2), published_at=None)]
    db = MagicMock()
    db.query.side_effect = lambda model: MagicMock(**{"filter.return_value.all.return_value": [job]}) if model is PublishJob else source_db.query(model)
    monkeypatch.setattr(cleanup, "_job_go_live_pending", lambda *args: {10} if live_pending else set())
    assert cleanup._cleanup_facebook_photos(db, now) == int(delete)
    assert photo.exists() is not delete
    assert (idea.image_local_path is None) is delete
