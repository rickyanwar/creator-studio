import json
import sqlite3
import pytest
from datetime import datetime, timezone
from unittest.mock import MagicMock

from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

# Allow SQLite to adapt list/dict objects and compile PostgreSQL ARRAY and JSONB types
sqlite3.register_adapter(list, json.dumps)
sqlite3.register_adapter(dict, json.dumps)
SQLiteTypeCompiler.visit_ARRAY = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_JSONB = lambda self, type_, **kw: "TEXT"
SQLiteTypeCompiler.visit_UUID = lambda self, type_, **kw: "TEXT"

from app.models.publish_jobs import ContentType, PublishJob, PublishJobStatus
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.models.posts import Post
from app.services.social_delivery_dispatch import (
    PLATFORM_ACCEPTED_MEDIA,
    PLATFORM_CAPTION_LIMITS,
    build_youtube_title,
    dispatch_phone_delivery,
    dispatch_repliz_delivery,
    truncate_caption_for_platform,
)
from app.services.social_delivery_planner import ensure_deliveries
from app.tasks.fanpage_sync import sync_fanpages_from_repliz
from fastapi.testclient import TestClient
from app.main import app
from app.api.deps import get_db, get_current_user
import app.api.social_brands as social_brands
import app.api.social_media as social_media


@pytest.fixture
def db_session():
    """Create clean in-memory SQLite schema and session."""
    sd_col = SocialDestination.__table__.columns["supported_media"]
    orig_sd = sd_col.server_default
    sd_col.server_default = None

    de_col = DeliveryEvent.__table__.columns["details"]
    orig_de = de_col.server_default
    de_col.server_default = None

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TargetFanpage.__table__.create(engine)
    SocialDestination.__table__.create(engine)
    PhoneDevice.__table__.create(engine)
    DestinationDeviceAssignment.__table__.create(engine)
    Post.__table__.create(engine)
    PublishJob.__table__.create(engine)
    PublishDelivery.__table__.create(engine)
    DeliveryEvent.__table__.create(engine)

    sd_col.server_default = orig_sd
    de_col.server_default = orig_de

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


def test_check_constraint_accepts_new_kinds(db_session: Session):
    """CHECK constraint accepts instagram, youtube, threads, x and rejects invalid kinds."""
    project = TargetFanpage(name="Test Brand", repliz_account_id="rep_b1", is_active=True)
    db_session.add(project)
    db_session.flush()

    for kind in ("facebook_page", "facebook_profile", "instagram", "youtube", "threads", "x"):
        dest = SocialDestination(
            content_project_id=project.id,
            kind=kind,
            name=f"Channel {kind}",
            mode="repliz",
            timezone="UTC",
            daily_target=1,
            repliz_daily_target=1,
            repliz_account_id=f"rep_{kind}",
            is_active=True,
        )
        db_session.add(dest)
        db_session.flush()
        assert dest.id is not None

    # Invalid kind must fail CHECK constraint
    invalid_dest = SocialDestination(
        content_project_id=project.id,
        kind="tiktok",
        name="Invalid Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
    )
    db_session.add(invalid_dest)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_caption_rules_hashtag_drop_and_limits():
    """Caption rules drop hashtags from end until it fits; enforces Instagram hashtag count limit."""
    # 1. Threads limit 500 chars: caption 490 chars + hashtags
    # Dropping #speed and #racing leaves 494 chars which fits <= 500
    base_text = "A" * 490
    caption_threads = f"{base_text} #f1 #racing #speed"
    res, ok, err = truncate_caption_for_platform(caption_threads, "threads")
    assert ok is True
    assert err is None
    assert len(res) <= 500
    assert "#speed" not in res
    assert "#racing" not in res
    assert res == f"{base_text} #f1"

    # All hashtags dropped if needed to fit
    base_text_long = "A" * 498
    caption_all_dropped = f"{base_text_long} #tag1 #tag2"
    res_all, ok_all, _ = truncate_caption_for_platform(caption_all_dropped, "threads")
    assert ok_all is True
    assert res_all == base_text_long

    # 2. X limit 280 chars: caption 260 chars + 2 hashtags
    base_x = "B" * 260
    caption_x = f"{base_x} #motorsport #verstappen"
    res_x, ok, err = truncate_caption_for_platform(caption_x, "x")
    assert ok is True
    assert len(res_x) <= 280
    assert "#verstappen" not in res_x

    # 3. Instagram limit 30 hashtags: caption with 35 hashtags
    tags = " ".join(f"#tag{i}" for i in range(35))
    caption_ig = f"Hello world {tags}"
    res_ig, ok, err = truncate_caption_for_platform(caption_ig, "instagram")
    assert ok is True
    # Count hashtags in result
    found_tags = [w for w in res_ig.split() if w.startswith("#")]
    assert len(found_tags) == 30
    assert "#tag34" not in res_ig
    assert "#tag0" in res_ig


def test_caption_too_long_without_hashtags_fails_closed():
    """If text without hashtags is still too long, fails closed and returns error."""
    # X limit 280
    too_long_text = "Word " * 60  # 300 chars
    res, ok, err = truncate_caption_for_platform(too_long_text, "x")
    assert ok is False
    assert err == "Caption longer than the x limit (280)"

    # Threads limit 500
    too_long_threads = "Word " * 110  # 550 chars
    res, ok, err = truncate_caption_for_platform(too_long_threads, "threads")
    assert ok is False
    assert err == "Caption longer than the threads limit (500)"

    # Instagram limit 2200
    too_long_ig = "Word " * 500  # 2500 chars
    res, ok, err = truncate_caption_for_platform(too_long_ig, "instagram")
    assert ok is False
    assert err == "Caption longer than the instagram limit (2200)"


def test_quotes_never_cut_youtube_title():
    """Quotes are never cut or shortened in YouTube titles; falls back to brand name + date."""
    # 1. Title <= 100 fits as is
    short_title = "Max Verstappen wins Monaco GP 2026"
    assert build_youtube_title(short_title, "F1 Fanpage", "2026-10-18") == short_title

    # 2. Title > 100 that would cut inside quote uses brand name + date
    prefix = "Team principal stated at the press conference today: "  # 53 chars
    quote = "'We had the pace all weekend and nothing was going to stop our drivers from securing a 1-2 finish here!'"  # 102 chars
    long_quote_title = prefix + quote  # 155 chars
    yt_title = build_youtube_title(long_quote_title, "Scuderia Fans", "2026-10-18")
    assert yt_title == "Scuderia Fans 2026-10-18"
    assert "'" not in yt_title
    assert len(yt_title) <= 100

    # 3. Title > 100 with double quotes crossing boundary
    long_double_quote = 'Leader declared: "We will continue fighting until the very last lap of the championship in Abu Dhabi!"' * 2
    yt_title2 = build_youtube_title(long_double_quote, "Race Day", "2026-10-18")
    assert yt_title2 == "Race Day 2026-10-18"

    # 4. Title > 100 with NO quotes truncates cleanly at word boundary
    clean_headline = (
        "Ferrari announces dramatic engineering upgrade package for upcoming European races after simulator test results"
    )
    yt_title3 = build_youtube_title(clean_headline, "F1 News", "2026-10-18")
    assert len(yt_title3) <= 100
    assert not yt_title3.endswith(" ")
    assert yt_title3 == "Ferrari announces dramatic engineering upgrade package for upcoming European races after simulator"


def test_youtube_photo_skipped_media_not_accepted(db_session: Session):
    """YouTube channel rejects photos/albums at planning and dispatch; marks skipped with reason."""
    project = TargetFanpage(name="YT Project", repliz_account_id="rep_yt_proj", is_active=True)
    db_session.add(project)
    db_session.flush()

    yt_dest = SocialDestination(
        content_project_id=project.id,
        kind="youtube",
        name="YouTube Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        supported_media=["video"],
        repliz_account_id="rep_yt_channel",
        is_active=True,
    )
    db_session.add(yt_dest)
    db_session.flush()

    # Image job
    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/photo.jpg",
        ai_generated_caption="News card photo",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    # 1. Planner marks skipped immediately
    deliveries = ensure_deliveries(db_session, job)
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.state == "skipped"
    assert "Media not accepted by youtube" in (d.last_error or "")

    # 2. Dispatcher also rejects if directly dispatched
    mock_client = MagicMock()
    d.state = "publishing"
    dispatch_repliz_delivery(db_session, d, repliz_client=mock_client)
    assert d.state == "skipped"
    assert "Media not accepted by youtube" in (d.last_error or "")
    mock_client.create_image_schedule.assert_not_called()
    mock_client.create_video_schedule.assert_not_called()


def test_x_album_accepted(db_session: Session):
    """X channel accepts multi-image album; plans scheduled delivery and dispatches album schedule to Repliz."""
    assert PLATFORM_ACCEPTED_MEDIA["x"] == {"image", "album", "video"}

    project = TargetFanpage(name="X Project", repliz_account_id="rep_x_proj", is_active=True)
    db_session.add(project)
    db_session.flush()

    x_dest = SocialDestination(
        content_project_id=project.id,
        kind="x",
        name="X Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        supported_media=["image", "album", "video"],
        repliz_account_id="rep_x_channel",
        is_active=True,
    )
    db_session.add(x_dest)
    db_session.flush()

    job = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.ig_repost,
        ai_generated_caption="Album on X",
        watermarked_image_urls=["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"],
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job)
    db_session.flush()

    # 1. Planner plans it as scheduled (not skipped)
    deliveries = ensure_deliveries(db_session, job)
    assert len(deliveries) == 1
    d = deliveries[0]
    assert d.state == "scheduled"
    assert d.media_kind == "album"

    # 2. Dispatcher calls create_album_schedule on repliz_client
    mock_client = MagicMock()
    mock_client.create_album_schedule.return_value = {"_id": "rep_sched_x_album"}
    d.state = "publishing"
    dispatch_repliz_delivery(db_session, d, repliz_client=mock_client)
    assert d.state == "repliz_scheduled"
    assert d.repliz_schedule_id == "rep_sched_x_album"
    mock_client.create_album_schedule.assert_called_once()
    call_kwargs = mock_client.create_album_schedule.call_args[1]
    assert call_kwargs["account_id"] == "rep_x_channel"
    assert call_kwargs["image_urls"] == ["https://cdn.example.com/1.jpg", "https://cdn.example.com/2.jpg"]


def test_payload_per_platform_mock_repliz(db_session: Session):
    """Verify schedule payload shape per platform via Repliz client without real network calls."""
    project = TargetFanpage(name="Brand 1", repliz_account_id="rep_b1", is_active=True)
    db_session.add(project)
    db_session.flush()

    mock_client = MagicMock()
    mock_client.create_video_schedule.return_value = {"_id": "rep_sched_yt"}
    mock_client.create_image_schedule.return_value = {"_id": "rep_sched_img"}

    now = datetime(2026, 10, 18, 12, 0, 0, tzinfo=timezone.utc)

    # 1. YouTube video delivery
    yt_dest = SocialDestination(
        content_project_id=project.id,
        kind="youtube",
        name="YT Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        repliz_account_id="acc_yt",
        is_active=True,
    )
    db_session.add(yt_dest)
    db_session.flush()

    job_yt = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.youtube_clip,
        video_url="https://cdn.example.com/reel.mp4",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_yt)
    db_session.flush()

    d_yt = PublishDelivery(
        publish_job_id=job_yt.id,
        destination_id=yt_dest.id,
        state="publishing",
        intended_method="repliz",
        media_kind="video",
        media_snapshot=["https://cdn.example.com/reel.mp4"],
        title_snapshot="Highlight of the Season",
        caption_snapshot="Season recap video #motogp",
        scheduled_for=now.replace(tzinfo=None),
    )
    db_session.add(d_yt)
    db_session.commit()

    dispatch_repliz_delivery(db_session, d_yt, repliz_client=mock_client, now_utc=now)
    assert d_yt.state == "repliz_scheduled"
    assert d_yt.repliz_schedule_id == "rep_sched_yt"
    mock_client.create_video_schedule.assert_called_once_with(
        account_id="acc_yt",
        description="Season recap video #motogp",
        video_url="https://cdn.example.com/reel.mp4",
        thumbnail_url="",
        title="Highlight of the Season",
        schedule_at="2026-10-18T12:00:00.000Z",
    )

    # 2. X delivery with caption fitting <= 280
    x_dest = SocialDestination(
        content_project_id=project.id,
        kind="x",
        name="X Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        repliz_account_id="acc_x",
        is_active=True,
    )
    db_session.add(x_dest)
    db_session.flush()

    job_x = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/card.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_x)
    db_session.flush()

    d_x = PublishDelivery(
        publish_job_id=job_x.id,
        destination_id=x_dest.id,
        state="publishing",
        intended_method="repliz",
        media_kind="image",
        media_snapshot=["https://cdn.example.com/card.jpg"],
        caption_snapshot="Breaking news on track today! #f1",
        scheduled_for=now.replace(tzinfo=None),
    )
    db_session.add(d_x)
    db_session.commit()

    dispatch_repliz_delivery(db_session, d_x, repliz_client=mock_client, now_utc=now)
    assert d_x.state == "repliz_scheduled"
    assert d_x.repliz_schedule_id == "rep_sched_img"
    mock_client.create_image_schedule.assert_called_once_with(
        account_id="acc_x",
        description="Breaking news on track today! #f1",
        image_url="https://cdn.example.com/card.jpg",
        alt="",
        schedule_at="2026-10-18T12:00:00.000Z",
    )

    # 3. Non-facebook channel rejected from phone delivery
    job_phone = PublishJob(
        fanpage_id=project.id,
        content_type=ContentType.news_content,
        design_image_url="https://cdn.example.com/card2.jpg",
        status=PublishJobStatus.pending_publish,
    )
    db_session.add(job_phone)
    db_session.flush()

    d_phone = PublishDelivery(
        publish_job_id=job_phone.id,
        destination_id=x_dest.id,
        state="scheduled",
        intended_method="phone",
        media_kind="image",
        media_snapshot=["https://cdn.example.com/card2.jpg"],
    )
    db_session.add(d_phone)
    db_session.commit()

    dispatch_phone_delivery(db_session, d_phone, now_utc=now)
    assert d_phone.state == "failed"
    assert "x does not support phone publishing" in d_phone.last_error


@pytest.fixture
def api_client(db_session):
    """FastAPI test client with DB and user dependency overrides."""
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: "test_user"
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_sync_ignores_non_facebook_accounts(db_session: Session, monkeypatch):
    """Repliz sync only writes Facebook accounts to target_fanpages and social_destinations."""
    class MockClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_facebook_accounts(self):
            return [
                {
                    "_id": "fb_sync_1",
                    "name": "Facebook Brand",
                    "username": "fbbrand",
                    "picture": "https://img.com/fb.png",
                    "isConnected": True,
                }
            ]

    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: MockClient(),
    )

    result = sync_fanpages_from_repliz(db=db_session)
    assert result["created"] == 1

    # Facebook account created
    fps = db_session.query(TargetFanpage).all()
    assert len(fps) == 1
    assert fps[0].repliz_account_id == "fb_sync_1"
    assert fps[0].platform_type == "facebook"

    dests = db_session.query(SocialDestination).all()
    assert len(dests) == 1
    assert dests[0].repliz_account_id == "fb_sync_1"
    assert dests[0].kind == "facebook_page"


def test_repliz_accounts_endpoint_returns_types(api_client, monkeypatch):
    """GET /social-media/repliz-accounts returns accounts with platform types (facebook, instagram, youtube, threads, x) mapping twitter->x and x->x."""
    class MockReplizAccountsClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_accounts(self, types=None, page=1, limit=50):
            return [
                {"_id": "acc_1", "name": "FB Page", "type": "facebook"},
                {"_id": "acc_2", "name": "IG Page", "type": "instagram"},
                {"_id": "acc_3", "name": "YT Channel", "type": "youtube"},
                {"_id": "acc_4", "name": "Threads Acc", "type": "threads"},
                {"_id": "acc_5", "name": "X Account 1", "type": "twitter"},
                {"_id": "acc_6", "name": "X Account 2", "type": "x"},
            ]

    monkeypatch.setattr(
        social_brands,
        "get_repliz_client_from_db",
        lambda db: MockReplizAccountsClient(),
    )

    resp = api_client.get("/social-media/repliz-accounts")
    assert resp.status_code == 200
    data = resp.json()
    assert data["configured"] is True
    assert data["error"] is None
    accounts = data["accounts"]
    assert len(accounts) == 6

    type_map = {a["id"]: a["type"] for a in accounts}
    assert type_map["acc_1"] == "facebook"
    assert type_map["acc_2"] == "instagram"
    assert type_map["acc_3"] == "youtube"
    assert type_map["acc_4"] == "threads"
    assert type_map["acc_5"] == "x"
    assert type_map["acc_6"] == "x"


def test_repliz_client_list_accounts_no_unknown_types_and_platform_mapping():
    """ReplizClient.list_accounts sends only documented types, makes extra call for twitter/x, and maps twitter/x -> platform 'x'."""
    from app.services.repliz_client import ReplizClient, REPLIZ_DOCUMENTED_ACCOUNT_TYPES

    client = ReplizClient(access_key="fake_key", secret_key="fake_sec", base_url="https://api.repliz.com")

    recorded_calls: list[dict] = []

    def mock_get(url: str, params: dict | None = None, **kwargs):
        recorded_calls.append({"url": url, "params": dict(params or {})})
        if params and any(k.startswith("types") for k in params):
            return MagicMock(
                raise_for_status=lambda: None,
                json=lambda: {
                    "docs": [
                        {"_id": "acc_fb", "name": "FB Page", "type": "facebook"},
                        {"_id": "acc_ig", "name": "IG Page", "type": "instagram"},
                    ]
                },
            )
        return MagicMock(
            raise_for_status=lambda: None,
            json=lambda: {
                "docs": [
                    {"_id": "acc_fb", "name": "FB Page", "type": "facebook"},
                    {"_id": "acc_tw", "name": "Twitter Account", "type": "twitter"},
                    {"_id": "acc_x", "name": "X Account", "type": "x"},
                    {"_id": "acc_li", "name": "LinkedIn Account", "type": "linkedin"},
                ]
            },
        )

    client._client.get = mock_get

    accounts = client.list_accounts()

    # Verify 2 calls
    assert len(recorded_calls) == 2

    # 1. Filtered request sends only documented types, NO unknown types (no twitter, no x)
    call1_params = recorded_calls[0]["params"]
    types_sent = [v for k, v in call1_params.items() if k.startswith("types")]
    assert set(types_sent) == set(REPLIZ_DOCUMENTED_ACCOUNT_TYPES)
    assert "twitter" not in types_sent
    assert "x" not in types_sent

    # 2. Second request has NO types filter
    call2_params = recorded_calls[1]["params"]
    assert not any(k.startswith("types") for k in call2_params)

    # 3. Client-side filtering and merging
    acc_by_id = {a["_id"]: a for a in accounts}
    assert len(acc_by_id) == 4
    assert "acc_li" not in acc_by_id

    # 4. Platform mapping
    assert acc_by_id["acc_tw"]["platform"] == "x"
    assert acc_by_id["acc_x"]["platform"] == "x"
    assert acc_by_id["acc_fb"]["platform"] == "facebook"
    assert acc_by_id["acc_ig"]["platform"] == "instagram"

    # 5. list_facebook_accounts does not make extra call or send unknown types
    recorded_calls.clear()
    fb_accounts = client.list_facebook_accounts()
    assert len(recorded_calls) == 1
    assert recorded_calls[0]["params"].get("types[0]") == "facebook"
    assert not any(k.startswith("types") and v in ("twitter", "x") for k, v in recorded_calls[0]["params"].items())


def test_create_destination_validates_kind_vs_account_type(api_client, db_session, monkeypatch):
    """POST /social-media/destinations validates kind matches Repliz account type and mode is repliz."""
    project = TargetFanpage(name="Multi Brand", repliz_account_id="rep_brand_main", is_active=True)
    db_session.add(project)
    db_session.commit()

    class MockClient:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def list_accounts(self, types=None, page=1, limit=50):
            return [
                {"_id": "rep_ig_acc", "name": "IG Profile", "type": "instagram"},
                {"_id": "rep_yt_acc", "name": "YT Profile", "type": "youtube"},
            ]

    monkeypatch.setattr(
        "app.services.repliz_client.get_repliz_client_from_db",
        lambda db: MockClient(),
    )

    # 1. Instagram with phone mode rejected
    resp1 = api_client.post(
        "/social-media/destinations",
        json={
            "content_project_id": project.id,
            "kind": "instagram",
            "name": "My IG Channel",
            "mode": "phone",
            "repliz_account_id": "rep_ig_acc",
            "daily_target": 1,
            "repliz_daily_target": 1,
        },
    )
    assert resp1.status_code == 400
    assert "must use repliz mode" in resp1.json()["detail"].lower()

    # 2. Instagram with YouTube account rejected (mismatch)
    resp2 = api_client.post(
        "/social-media/destinations",
        json={
            "content_project_id": project.id,
            "kind": "instagram",
            "name": "My IG Channel",
            "mode": "repliz",
            "repliz_account_id": "rep_yt_acc",
            "daily_target": 1,
            "repliz_daily_target": 1,
        },
    )
    assert resp2.status_code == 400
    assert "does not match kind 'instagram'" in resp2.json()["detail"].lower()

    # 3. Instagram with matching Instagram account succeeded
    resp3 = api_client.post(
        "/social-media/destinations",
        json={
            "content_project_id": project.id,
            "kind": "instagram",
            "name": "My IG Channel",
            "mode": "repliz",
            "repliz_account_id": "rep_ig_acc",
            "daily_target": 1,
            "repliz_daily_target": 1,
            "supported_media": ["image", "album", "video"],
        },
    )
    assert resp3.status_code == 201
    created_dest = resp3.json()
    assert created_dest["kind"] == "instagram"
    assert created_dest["mode"] == "repliz"
    assert created_dest["repliz_account_id"] == "rep_ig_acc"


def test_patch_destination_keeps_platform_kind(api_client, db_session):
    """PATCH destination keeps instagram / non-fb kind when pacing or other settings update."""
    project = TargetFanpage(name="Pacing Brand", repliz_account_id="rep_brand_pacing", is_active=True)
    db_session.add(project)
    db_session.commit()

    ig_dest = SocialDestination(
        content_project_id=project.id,
        kind="instagram",
        name="Instagram Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=2,
        repliz_daily_target=2,
        repliz_account_id="rep_ig_pacing",
        is_active=True,
    )
    db_session.add(ig_dest)
    db_session.commit()

    # 1. PATCH pacing (daily_target, min_gap_minutes) without specifying kind
    resp = api_client.patch(
        f"/social-media/destinations/{ig_dest.id}",
        json={"daily_target": 6, "min_gap_minutes": 20},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "instagram"
    assert data["daily_target"] == 6
    assert data["min_gap_minutes"] == 20

    db_session.refresh(ig_dest)
    assert ig_dest.kind == "instagram"
    assert ig_dest.daily_target == 6

    # 2. PATCH with explicit kind="instagram" keeps instagram
    resp2 = api_client.patch(
        f"/social-media/destinations/{ig_dest.id}",
        json={"kind": "instagram", "name": "Renamed IG Channel"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["kind"] == "instagram"
    assert resp2.json()["name"] == "Renamed IG Channel"

    db_session.refresh(ig_dest)
    assert ig_dest.kind == "instagram"


def test_patch_facebook_page_unchanged(api_client, db_session):
    """PATCH facebook page keeps facebook_page kind unchanged."""
    project = TargetFanpage(name="FB Brand", repliz_account_id="rep_brand_fb", is_active=True)
    db_session.add(project)
    db_session.commit()

    fb_dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="Facebook Page Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=3,
        repliz_daily_target=3,
        repliz_account_id="rep_fb_pacing",
        is_active=True,
    )
    db_session.add(fb_dest)
    db_session.commit()

    # 1. PATCH pacing without kind
    resp = api_client.patch(
        f"/social-media/destinations/{fb_dest.id}",
        json={"daily_target": 7},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["kind"] == "facebook_page"
    assert data["daily_target"] == 7

    db_session.refresh(fb_dest)
    assert fb_dest.kind == "facebook_page"

    # 2. PATCH with kind="page" or "facebook_page" keeps facebook_page
    resp2 = api_client.patch(
        f"/social-media/destinations/{fb_dest.id}",
        json={"kind": "page"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["kind"] == "facebook_page"

    db_session.refresh(fb_dest)
    assert fb_dest.kind == "facebook_page"


def test_switching_kind_between_facebook_and_non_facebook_returns_409(api_client, db_session):
    """Switching destination kind between Facebook and non-Facebook returns 409 Conflict."""
    project = TargetFanpage(name="Switch Brand", repliz_account_id="rep_brand_switch", is_active=True)
    db_session.add(project)
    db_session.commit()

    fb_dest = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="FB Page",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        repliz_account_id="rep_fb_sw",
        is_active=True,
    )
    fb_profile = SocialDestination(
        content_project_id=project.id,
        kind="facebook_profile",
        name="FB Profile",
        mode="phone",
        timezone="UTC",
        daily_target=1,
        phone_daily_target=1,
        facebook_actor_id="act_profile_1",
        is_active=False,
    )
    ig_dest = SocialDestination(
        content_project_id=project.id,
        kind="instagram",
        name="IG Channel",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        repliz_account_id="rep_ig_sw",
        is_active=True,
    )
    db_session.add_all([fb_dest, fb_profile, ig_dest])
    db_session.commit()

    # 1. FB page -> Instagram => 409
    resp_fb_to_ig = api_client.patch(
        f"/social-media/destinations/{fb_dest.id}",
        json={"kind": "instagram"},
    )
    assert resp_fb_to_ig.status_code == 409
    assert "between facebook and non-facebook" in resp_fb_to_ig.json()["detail"].lower()

    # 2. FB profile -> Threads => 409
    resp_prof_to_th = api_client.patch(
        f"/social-media/destinations/{fb_profile.id}",
        json={"kind": "threads"},
    )
    assert resp_prof_to_th.status_code == 409
    assert "between facebook and non-facebook" in resp_prof_to_th.json()["detail"].lower()

    # 3. Instagram -> FB page => 409
    resp_ig_to_fb = api_client.patch(
        f"/social-media/destinations/{ig_dest.id}",
        json={"kind": "facebook_page"},
    )
    assert resp_ig_to_fb.status_code == 409
    assert "between facebook and non-facebook" in resp_ig_to_fb.json()["detail"].lower()

    # 4. Instagram -> FB profile => 409
    resp_ig_to_prof = api_client.patch(
        f"/social-media/destinations/{ig_dest.id}",
        json={"kind": "profile"},
    )
    assert resp_ig_to_prof.status_code == 409
    assert "between facebook and non-facebook" in resp_ig_to_prof.json()["detail"].lower()


def test_list_destinations_filters_by_platform_kind(api_client, db_session):
    """GET /social-media/destinations filters properly for new platform kinds and aliases."""
    project = TargetFanpage(name="Filter Brand", repliz_account_id="rep_filter_b", is_active=True)
    db_session.add(project)
    db_session.commit()

    fb_page = SocialDestination(
        content_project_id=project.id,
        kind="facebook_page",
        name="FB Page",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        repliz_account_id="rep_fb_f",
        is_active=True,
    )
    ig_dest = SocialDestination(
        content_project_id=project.id,
        kind="instagram",
        name="IG Dest",
        mode="repliz",
        timezone="UTC",
        daily_target=1,
        repliz_daily_target=1,
        repliz_account_id="rep_ig_f",
        is_active=True,
    )
    db_session.add_all([fb_page, ig_dest])
    db_session.commit()

    # Query kind=instagram returns only instagram
    resp_ig = api_client.get(f"/social-media/destinations?content_project_id={project.id}&kind=instagram")
    assert resp_ig.status_code == 200
    items_ig = resp_ig.json()
    assert len(items_ig) == 1
    assert items_ig[0]["kind"] == "instagram"

    # Query kind=facebook_page returns only facebook_page
    resp_fb = api_client.get(f"/social-media/destinations?content_project_id={project.id}&kind=facebook_page")
    assert resp_fb.status_code == 200
    items_fb = resp_fb.json()
    assert len(items_fb) == 1
    assert items_fb[0]["kind"] == "facebook_page"

    # Query kind=page returns facebook_page
    resp_page = api_client.get(f"/social-media/destinations?content_project_id={project.id}&kind=page")
    assert resp_page.status_code == 200
    items_page = resp_page.json()
    assert len(items_page) == 1
    assert items_page[0]["kind"] == "facebook_page"



