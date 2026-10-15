"""Tests for PLAN.md step 8 (R1) - Post reconciliation, statistics, activity API.

Covers:
- Repliz GET /public/content client contract:
  - Basic auth, accountId required, type=media query parameter (no limit param), nextToken.
  - Gold+ 402 raises ReplizPlanRequired.
  - Bounded nextToken pagination with type=media.
- Conservative reconciliation:
  - Exact match (unique match to destination.facebook_actor_id, caption, and time window).
  - Ambiguous match (>1 candidates match; must NOT invent an ID).
  - Foreign owner (candidate owner.id does not match facebook_actor_id; candidate rejected).
  - Time mismatch (candidate createdAt outside time window; candidate rejected).
  - Missing expected publish time (both scheduled_for and published_at absent; fails closed).
  - Missing facebook_actor_id on destination (fails closed; no invented ID).
  - Gold-tier error (ReplizPlanRequired caught, skipped event recorded, no ID invented).
- Distinct repliz_schedule_id vs facebook_post_id / permalink.
- Delivery ledger status updates via status_sync.
- Metrics selection preferring delivery ledger IDs over legacy repliz_response_json.
- Authenticated /social-media/activity serialization with state, methods, IDs/link, events.
"""

from datetime import datetime, timedelta, timezone
import json
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import DefaultClause

from app.api.deps import get_current_user, get_db
from app.main import app
from app.models.publish_jobs import PublishJob, PublishJobStatus
from app.models.post_metric_snapshots import PostMetricSnapshot
from app.models.social_publishing import (
    DeliveryEvent,
    DestinationDeviceAssignment,
    PhoneDevice,
    PublishDelivery,
    SocialDestination,
)
from app.models.target_fanpages import TargetFanpage
from app.models.users import User
from app.services.post_metrics import select_post_id_and_account, select_metric_targets
from app.services.repliz_client import ReplizClient, ReplizPlanRequired
from app.services.social_post_reconciliation import (
    DEFAULT_RECONCILIATION_WINDOW,
    ReconciliationResult,
    fetch_recent_content_bounded,
    match_single_content_item,
    normalize_caption,
    parse_repliz_datetime,
    reconcile_delivery_repliz,
)
from app.tasks.status_sync import (
    _REPLIZ_FAILURE_STATUSES,
    _REPLIZ_SUCCESS_STATUSES,
    sync_pending_deliveries,
)

# Compile PostgreSQL-specific column types for SQLite
@compiles(ARRAY, "sqlite")
def _compile_array(type_, compiler, **kw):
    return "TEXT"


@compiles(JSONB, "sqlite")
def _compile_jsonb(type_, compiler, **kw):
    return "JSON"


SocialDestination.__table__.columns["supported_media"].server_default = DefaultClause(
    text("'[\"image\", \"album\", \"video\"]'")
)
DeliveryEvent.__table__.columns["details"].server_default = DefaultClause(
    text("'{}'")
)


@pytest.fixture
def db_session():
    """Create isolated SQLite database session with required tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    tables = [
        TargetFanpage.__table__,
        PhoneDevice.__table__,
        SocialDestination.__table__,
        DestinationDeviceAssignment.__table__,
        PublishJob.__table__,
        PublishDelivery.__table__,
        DeliveryEvent.__table__,
        PostMetricSnapshot.__table__,
    ]
    for table in tables:
        table.create(bind=engine)

    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def client(db_session):
    """FastAPI TestClient with overridden DB and mock authenticated user."""
    mock_user = User(id=1, username="editor", password_hash="hash", is_active=True)
    app.dependency_overrides[get_db] = lambda: db_session
    app.dependency_overrides[get_current_user] = lambda: mock_user
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


# ── 1. ReplizClient get_content tests ─────────────────────────────────────────


class TestReplizGetContentClient:
    def test_get_content_contract(self):
        def mock_handler(request):
            assert request.url.path == "/public/content"
            # Contract: accountId and type=media (no limit param)
            q = request.url.query.decode()
            assert "accountId=rep_acc_123" in q
            assert "type=media" in q
            assert "limit" not in q
            assert request.headers.get("authorization", "").startswith("Basic ")
            return httpx.Response(
                200,
                json={
                    "docs": [
                        {
                            "id": "fb_post_999",
                            "owner": {"id": "fb_actor_456"},
                            "description": "Exclusive breaking news",
                            "url": "https://www.facebook.com/123/posts/999",
                            "createdAt": "2026-10-15T12:00:00.000Z",
                        }
                    ],
                    "nextToken": "next_token_abc",
                },
            )

        transport = httpx.MockTransport(mock_handler)
        cli = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
        cli._client = httpx.Client(base_url=cli.base_url, headers=cli._client.headers, transport=transport)

        res = cli.get_content(account_id="rep_acc_123", content_type="media")
        assert "docs" in res
        assert len(res["docs"]) == 1
        assert res["docs"][0]["id"] == "fb_post_999"
        assert res["docs"][0]["owner"]["id"] == "fb_actor_456"
        assert res["nextToken"] == "next_token_abc"

    def test_get_content_with_next_token(self):
        def mock_handler(request):
            q = request.url.query.decode()
            assert "nextToken=tok_456" in q
            assert "type=media" in q
            return httpx.Response(200, json={"docs": []})

        transport = httpx.MockTransport(mock_handler)
        cli = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
        cli._client = httpx.Client(base_url=cli.base_url, headers=cli._client.headers, transport=transport)

        res = cli.get_content(account_id="rep_acc_123", next_token="tok_456")
        assert res == {"docs": []}

    def test_get_content_402_plan_required(self):
        def mock_handler(request):
            return httpx.Response(402, text="Gold+ plan required for Content API")

        transport = httpx.MockTransport(mock_handler)
        cli = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
        cli._client = httpx.Client(base_url=cli.base_url, headers=cli._client.headers, transport=transport)

        with pytest.raises(ReplizPlanRequired, match="Gold\\+ plan required"):
            cli.get_content(account_id="rep_acc_123")

    def test_get_content_missing_account_id_raises_value_error(self):
        cli = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
        with pytest.raises(ValueError, match="accountId is required"):
            cli.get_content(account_id="")

    def test_bounded_next_token_pagination(self):
        pages_called = 0

        def mock_handler(request):
            nonlocal pages_called
            pages_called += 1
            q = request.url.query.decode()
            if pages_called == 1:
                assert "nextToken" not in q
                return httpx.Response(200, json={"docs": [{"id": "p1"}], "nextToken": "tok_p2"})
            elif pages_called == 2:
                assert "nextToken=tok_p2" in q
                return httpx.Response(200, json={"docs": [{"id": "p2"}], "nextToken": "tok_p3"})
            else:
                assert "nextToken=tok_p3" in q
                return httpx.Response(200, json={"docs": [{"id": "p3"}], "nextToken": None})

        transport = httpx.MockTransport(mock_handler)
        cli = ReplizClient("ak", "sk", base_url="https://api.repliz.com")
        cli._client = httpx.Client(base_url=cli.base_url, headers=cli._client.headers, transport=transport)

        items = fetch_recent_content_bounded(cli, account_id="acc1", max_pages=3)
        assert len(items) == 3
        assert [i["id"] for i in items] == ["p1", "p2", "p3"]
        assert pages_called == 3


# ── 2. Conservative reconciliation pure matching tests ────────────────────────


class TestConservativeReconciliationPure:
    def test_exact_unique_match_compares_actor_id(self):
        expected_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        items = [
            {
                "id": "fb_post_101",
                # owner.id is the Facebook actor/Page ID, NOT the repliz_account_id
                "owner": {"id": "fb_actor_9988"},
                "description": "Grand Prix preview: Ferrari vs Red Bull",
                "url": "https://facebook.com/page1/posts/101",
                "createdAt": "2026-10-15T12:05:00.000Z",
            }
        ]
        res = match_single_content_item(
            items=items,
            expected_actor_id="fb_actor_9988",
            expected_caption="Grand Prix preview: Ferrari vs Red Bull",
            expected_time=expected_time,
        )
        assert res.matched is True
        assert res.status == "exact_match"
        assert res.content_id == "fb_post_101"
        assert res.permalink == "https://facebook.com/page1/posts/101"

    def test_ambiguous_multiple_candidates_no_invented_id(self):
        expected_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        items = [
            {
                "id": "fb_post_101",
                "owner": {"id": "fb_actor_9988"},
                "description": "Same duplicate caption",
                "url": "https://facebook.com/page1/posts/101",
                "createdAt": "2026-10-15T12:05:00.000Z",
            },
            {
                "id": "fb_post_102",
                "owner": {"id": "fb_actor_9988"},
                "description": "Same duplicate caption",
                "url": "https://facebook.com/page1/posts/102",
                "createdAt": "2026-10-15T12:10:00.000Z",
            },
        ]
        res = match_single_content_item(
            items=items,
            expected_actor_id="fb_actor_9988",
            expected_caption="Same duplicate caption",
            expected_time=expected_time,
        )
        assert res.matched is False
        assert res.status == "ambiguous"
        assert res.content_id is None
        assert res.candidate_count == 2

    def test_foreign_owner_rejection(self):
        expected_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        items = [
            {
                "id": "fb_post_foreign",
                # Belongs to a different Facebook Page
                "owner": {"id": "fb_actor_different_1111"},
                "description": "Exact caption match",
                "url": "https://facebook.com/foreign/posts/1",
                "createdAt": "2026-10-15T12:00:00.000Z",
            }
        ]
        res = match_single_content_item(
            items=items,
            expected_actor_id="fb_actor_9988",
            expected_caption="Exact caption match",
            expected_time=expected_time,
        )
        assert res.matched is False
        assert res.status == "foreign_owner"
        assert res.content_id is None

    def test_time_mismatch_rejection(self):
        expected_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        # 6 hours later - outside the 2 hour tolerance
        items = [
            {
                "id": "fb_post_old",
                "owner": {"id": "fb_actor_9988"},
                "description": "Timeless caption",
                "url": "https://facebook.com/page1/posts/old",
                "createdAt": "2026-10-15T18:00:00.000Z",
            }
        ]
        res = match_single_content_item(
            items=items,
            expected_actor_id="fb_actor_9988",
            expected_caption="Timeless caption",
            expected_time=expected_time,
            time_window=timedelta(hours=2),
        )
        assert res.matched is False
        assert res.status == "time_mismatch"
        assert res.content_id is None

    def test_missing_actor_id_fails_closed(self):
        expected_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        items = [
            {
                "id": "fb_post_101",
                "owner": {"id": "fb_actor_9988"},
                "description": "Valid caption",
                "createdAt": "2026-10-15T12:00:00.000Z",
            }
        ]
        res = match_single_content_item(
            items=items,
            expected_actor_id=None,
            expected_caption="Valid caption",
            expected_time=expected_time,
        )
        assert res.matched is False
        assert res.status == "no_match"
        assert res.content_id is None
        assert "verified facebook_actor_id" in res.detail

    def test_empty_expected_caption_fails_closed(self):
        expected_time = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
        items = [
            {
                "id": "fb_post_blank",
                "owner": {"id": "fb_actor_9988"},
                "description": "",
                "createdAt": "2026-10-15T12:00:00.000Z",
            }
        ]
        res = match_single_content_item(
            items=items,
            expected_actor_id="fb_actor_9988",
            expected_caption="",
            expected_time=expected_time,
        )
        assert res.matched is False
        assert res.status == "no_match"
        assert res.content_id is None


# ── 3. DB Delivery Reconciliation & Gold-tier Error Handling ─────────────────


class TestReconcileDeliveryReplizDB:
    def _create_setup(
        self,
        db_session,
        repliz_account_id="rep_acc_42",
        facebook_actor_id="fb_page_actor_12345",
        scheduled_for=datetime(2026, 10, 15, 14, 0),
        published_at=None,
    ):
        project = TargetFanpage(
            name="Formula 1 Fan Hub",
            timezone="Europe/London",
            repliz_account_id=repliz_account_id,
        )
        db_session.add(project)
        db_session.flush()

        dest = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="F1 Page",
            facebook_actor_id=facebook_actor_id,
            mode="repliz",
            repliz_account_id=repliz_account_id,
            timezone="Europe/London",
            daily_target=1,
            repliz_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        db_session.add(dest)
        db_session.flush()

        job = PublishJob(
            fanpage_id=project.id,
            status=PublishJobStatus.published,
            scheduled_for=scheduled_for,
        )
        db_session.add(job)
        db_session.flush()

        delivery = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="repliz_scheduled",
            actual_method="repliz",
            repliz_schedule_id="sched_98765",
            scheduled_for=scheduled_for,
            published_at=published_at,
            caption_snapshot="Full race results from Monza #F1",
            media_kind="image",
        )
        db_session.add(delivery)
        db_session.commit()
        db_session.refresh(delivery)
        return project, dest, job, delivery

    def test_reconciliation_exact_match_updates_delivery_distinct_ids(self, db_session):
        _, dest, _, delivery = self._create_setup(db_session)

        fake_client = type("FakeClient", (), {})()
        fake_client.get_content = lambda account_id, content_type="media", next_token=None: {
            "docs": [
                {
                    "id": "fb_real_post_777",
                    # owner.id matches destination.facebook_actor_id
                    "owner": {"id": dest.facebook_actor_id},
                    "description": "Full race results from Monza #F1",
                    "url": "https://www.facebook.com/f1/posts/777",
                    "createdAt": "2026-10-15T14:02:00.000Z",
                }
            ],
            "nextToken": None,
        }

        res = reconcile_delivery_repliz(db_session, delivery, client=fake_client)
        assert res.matched is True
        assert res.content_id == "fb_real_post_777"

        db_session.refresh(delivery)
        # Verify IDs stay strictly distinct
        assert delivery.repliz_schedule_id == "sched_98765"
        assert delivery.facebook_post_id == "fb_real_post_777"
        assert delivery.facebook_permalink == "https://www.facebook.com/f1/posts/777"
        assert delivery.state == "published"

        # Verify event was recorded
        events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
        assert any(e.event_type == "reconciled" for e in events)

    def test_reconciliation_gold_tier_plan_required(self, db_session):
        _, _, _, delivery = self._create_setup(db_session)

        def raise_402(account_id, content_type="media", next_token=None):
            raise ReplizPlanRequired("Gold plan required")

        fake_client = type("FakeClient", (), {})()
        fake_client.get_content = raise_402

        res = reconcile_delivery_repliz(db_session, delivery, client=fake_client)
        assert res.matched is False
        assert res.status == "plan_required"

        db_session.refresh(delivery)
        # ID is not invented!
        assert delivery.facebook_post_id is None
        assert delivery.repliz_schedule_id == "sched_98765"

        # Verify skipped event recorded
        events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
        assert any(e.event_type == "reconciliation_skipped" and e.details.get("reason") == "plan_required" for e in events)

    def test_reconciliation_fails_closed_when_both_times_absent(self, db_session):
        # scheduled_for=None and published_at=None
        _, _, _, delivery = self._create_setup(db_session, scheduled_for=None, published_at=None)

        fake_client = type("FakeClient", (), {})()
        fake_client.get_content = lambda account_id, content_type="media", next_token=None: {
            "docs": [{"id": "p1"}]
        }

        res = reconcile_delivery_repliz(db_session, delivery, client=fake_client)
        assert res.matched is False
        assert res.status == "no_match"
        assert "scheduled_for or published_at" in res.detail

        db_session.refresh(delivery)
        assert delivery.facebook_post_id is None
        events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
        assert any(e.details.get("reason") == "missing_expected_publish_time" for e in events)

    def test_reconciliation_fails_closed_when_destination_actor_id_missing(self, db_session):
        # destination without facebook_actor_id
        _, _, _, delivery = self._create_setup(db_session, facebook_actor_id=None)

        fake_client = type("FakeClient", (), {})()
        fake_client.get_content = lambda account_id, content_type="media", next_token=None: {
            "docs": [{"id": "p1"}]
        }

        res = reconcile_delivery_repliz(db_session, delivery, client=fake_client)
        assert res.matched is False
        assert res.status == "no_match"
        assert "facebook_actor_id" in res.detail

        db_session.refresh(delivery)
        assert delivery.facebook_post_id is None
        events = db_session.query(DeliveryEvent).filter_by(delivery_id=delivery.id).all()
        assert any(e.details.get("reason") == "missing_facebook_actor_id" for e in events)


# ── 4. Ledger Status Sync & Updates ──────────────────────────────────────────


class TestLedgerStatusSync:
    def test_sync_pending_deliveries_success_with_schedule_post_id(self, db_session):
        project = TargetFanpage(name="P1", timezone="Europe/London")
        db_session.add(project)
        db_session.flush()

        dest = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="Page1",
            facebook_actor_id="actor_page1",
            mode="repliz",
            timezone="Europe/London",
            daily_target=1,
            repliz_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        db_session.add(dest)
        db_session.flush()

        job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.published)
        db_session.add(job)
        db_session.flush()

        delivery = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="repliz_scheduled",
            repliz_schedule_id="sched_111",
            scheduled_for=datetime(2026, 10, 15, 10, 0),
            media_kind="image",
        )
        db_session.add(delivery)
        db_session.commit()

        fake_client = type("FakeClient", (), {})()
        fake_client.get_schedule = lambda sid: {
            "status": "success",
            "postId": "123456_789012",
            "url": "https://facebook.com/123456/posts/789012",
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.tasks.status_sync.get_repliz_client_from_db", lambda db: fake_client)
            sync_now = datetime(2026, 10, 15, 10, 5, tzinfo=timezone.utc)
            synced = sync_pending_deliveries(db=db_session, now=sync_now)
            assert synced == 1

        db_session.refresh(delivery)
        assert delivery.state == "published"
        assert delivery.facebook_post_id == "123456_789012"
        assert delivery.facebook_permalink == "https://facebook.com/123456/posts/789012"
        assert delivery.repliz_schedule_id == "sched_111"

    def test_sync_pending_deliveries_failure(self, db_session):
        project = TargetFanpage(name="P2", timezone="Europe/London")
        db_session.add(project)
        db_session.flush()

        dest = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="Page2",
            facebook_actor_id="actor_page2",
            mode="repliz",
            timezone="Europe/London",
            daily_target=1,
            repliz_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        db_session.add(dest)
        db_session.flush()

        job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.published)
        db_session.add(job)
        db_session.flush()

        delivery = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="repliz_scheduled",
            repliz_schedule_id="sched_fail",
            scheduled_for=datetime(2026, 10, 15, 10, 0),
            media_kind="image",
        )
        db_session.add(delivery)
        db_session.commit()

        fake_client = type("FakeClient", (), {})()
        fake_client.get_schedule = lambda sid: {
            "status": "failed",
            "errorMessage": "Media upload timed out on Facebook CDN",
        }

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.tasks.status_sync.get_repliz_client_from_db", lambda db: fake_client)
            sync_now = datetime(2026, 10, 15, 10, 5, tzinfo=timezone.utc)
            synced = sync_pending_deliveries(db=db_session, now=sync_now)
            assert synced == 1

        db_session.refresh(delivery)
        assert delivery.state == "failed"
        assert "Facebook CDN" in delivery.last_error
        assert delivery.facebook_post_id is None

    def test_sync_pending_deliveries_no_due_returns_zero(self, db_session):
        project = TargetFanpage(name="P3", timezone="Europe/London")
        db_session.add(project)
        db_session.flush()

        dest = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="Page3",
            facebook_actor_id="actor_page3",
            mode="repliz",
            timezone="Europe/London",
            daily_target=1,
            repliz_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        db_session.add(dest)
        db_session.flush()

        job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.published)
        db_session.add(job)
        db_session.flush()

        delivery = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            state="repliz_scheduled",
            repliz_schedule_id="sched_future",
            scheduled_for=datetime(2026, 10, 15, 10, 0),
            media_kind="image",
        )
        db_session.add(delivery)
        db_session.commit()

        # Before due time (poll min age is 2 min, so 10:01 is not due)
        sync_now_early = datetime(2026, 10, 15, 10, 1, tzinfo=timezone.utc)
        synced = sync_pending_deliveries(db=db_session, now=sync_now_early)
        assert synced == 0


# ── 5. Metrics Selection Preference Tests ─────────────────────────────────────


class TestMetricsSelection:
    def test_prefers_delivery_ledger_id_over_legacy(self):
        fanpage = TargetFanpage(id=1, repliz_account_id="legacy_acc", name="FP")
        dest = SocialDestination(id=10, repliz_account_id="dest_acc", name="Dest")
        deliv = PublishDelivery(
            id=50,
            destination_id=10,
            facebook_post_id="999_888",
        )
        deliv.destination = dest

        job = PublishJob(
            id=100,
            fanpage_id=1,
            repliz_response_json={"postId": "111_222"},
        )
        job.fanpage = fanpage
        job.deliveries = [deliv]

        post_id, account_id = select_post_id_and_account(job)
        assert post_id == "999_888"
        assert account_id == "dest_acc"

    def test_falls_back_to_legacy_when_no_delivery_ledger_id(self):
        fanpage = TargetFanpage(id=1, repliz_account_id="legacy_acc", name="FP")
        job = PublishJob(
            id=100,
            fanpage_id=1,
            repliz_response_json={"postId": "111_222"},
        )
        job.fanpage = fanpage
        job.deliveries = []

        post_id, account_id = select_post_id_and_account(job)
        assert post_id == "111_222"
        assert account_id == "legacy_acc"

    def test_select_metric_targets_multiple_destinations(self):
        fanpage = TargetFanpage(id=1, repliz_account_id="legacy_acc", name="FP")
        dest1 = SocialDestination(id=10, repliz_account_id="acc_1", name="Dest 1")
        dest2 = SocialDestination(id=20, repliz_account_id="acc_2", name="Dest 2")
        deliv1 = PublishDelivery(id=101, destination_id=10, facebook_post_id="post_101")
        deliv1.destination = dest1
        deliv2 = PublishDelivery(id=102, destination_id=20, facebook_post_id="post_102")
        deliv2.destination = dest2

        job = PublishJob(id=50, fanpage_id=1)
        job.fanpage = fanpage
        job.deliveries = [deliv1, deliv2]

        targets = select_metric_targets(job)
        assert len(targets) == 2
        assert targets[0] == (101, "post_101", "acc_1")
        assert targets[1] == (102, "post_102", "acc_2")

    def test_two_destinations_one_job_distinct_snapshots(self, db_session):
        project = TargetFanpage(name="ProjectX", timezone="Europe/London", repliz_account_id="proj_acc")
        db_session.add(project)
        db_session.flush()

        dest1 = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="Dest 1",
            repliz_account_id="dest1_acc",
            mode="repliz",
            timezone="Europe/London",
            daily_target=1,
            min_gap_minutes=5,
        )
        dest2 = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="Dest 2",
            repliz_account_id="dest2_acc",
            mode="repliz",
            timezone="Europe/London",
            daily_target=1,
            min_gap_minutes=5,
        )
        db_session.add_all([dest1, dest2])
        db_session.flush()

        job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.published)
        db_session.add(job)
        db_session.flush()

        deliv1 = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest1.id,
            state="published",
            facebook_post_id="fb_post_1",
        )
        deliv2 = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest2.id,
            state="published",
            facebook_post_id="fb_post_2",
        )
        db_session.add_all([deliv1, deliv2])
        db_session.commit()

        # Both deliveries belong to the same job and receive snapshots for the same bucket "1h"
        from app.models.settings import Settings
        from app.tasks.post_metrics import _fetch_and_store

        settings = Settings(id=1, metrics_ingestion_enabled=True)
        now = datetime.now(timezone.utc).replace(tzinfo=None)

        fake_client = type("FakeClient", (), {})()
        stats_map = {
            ("fb_post_1", "dest1_acc"): {"like": 10, "comments": 1, "shares": 0},
            ("fb_post_2", "dest2_acc"): {"like": 25, "comments": 5, "shares": 3},
        }
        fake_client.get_content_statistic = lambda pid, acc: {"data": stats_map.get((pid, acc), {})}

        due_list = [
            (job, deliv1.id, "fb_post_1", "dest1_acc", "1h", 65),
            (job, deliv2.id, "fb_post_2", "dest2_acc", "1h", 65),
        ]

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.tasks.post_metrics.get_repliz_client_from_db", lambda db: fake_client)
            mp.setattr("app.tasks.post_metrics._CALL_PAUSE_SECONDS", 0)
            _fetch_and_store(db_session, settings, due_list, now)

        # Verify distinct snapshots stored for each delivery
        snaps = db_session.query(PostMetricSnapshot).filter_by(publish_job_id=job.id, bucket="1h").all()
        assert len(snaps) == 2

        snap1 = next(s for s in snaps if s.delivery_id == deliv1.id)
        assert snap1.likes == 10
        assert snap1.comments == 1

        snap2 = next(s for s in snaps if s.delivery_id == deliv2.id)
        assert snap2.likes == 25
        assert snap2.comments == 5

        # Re-running same due_list is idempotent due to partial index on (delivery_id, bucket)
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr("app.tasks.post_metrics.get_repliz_client_from_db", lambda db: fake_client)
            mp.setattr("app.tasks.post_metrics._CALL_PAUSE_SECONDS", 0)
            _fetch_and_store(db_session, settings, due_list, now)

        snaps_after = db_session.query(PostMetricSnapshot).filter_by(publish_job_id=job.id, bucket="1h").all()
        assert len(snaps_after) == 2


# ── 6. Celery Beat Schedule Status Sync Tasks ─────────────────────────────────


def test_beat_schedule_status_sync_tasks():
    from app.tasks.celery_app import celery_app
    sched = celery_app.conf.beat_schedule
    assert "sync-repliz-status" in sched
    assert sched["sync-repliz-status"]["task"] == "app.tasks.status_sync.sync_pending_schedules"
    assert sched["sync-repliz-status"]["schedule"] == 300

    assert "sync-delivery-status" in sched
    assert sched["sync-delivery-status"]["task"] == "app.tasks.status_sync.sync_pending_deliveries"
    assert sched["sync-delivery-status"]["schedule"] == 300


# ── 6. Activity API Endpoint & Serialization ──────────────────────────────────


class TestSocialMediaActivityEndpoint:
    def test_list_activity_serialization(self, client, db_session):
        project = TargetFanpage(name="Formula Hub", timezone="Europe/London")
        db_session.add(project)
        db_session.flush()

        device = PhoneDevice(name="Realme GT", status="available")
        db_session.add(device)
        db_session.flush()

        dest = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="Scuderia Page",
            facebook_actor_id="actor_scuderia",
            mode="mixed",
            timezone="Europe/London",
            daily_target=2,
            phone_daily_target=1,
            repliz_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        db_session.add(dest)
        db_session.flush()

        job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.published)
        db_session.add(job)
        db_session.flush()

        delivery = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest.id,
            device_id=device.id,
            state="published",
            intended_method="phone",
            actual_method="phone",
            scheduled_for=datetime(2026, 10, 15, 15, 30),
            claimed_at=datetime(2026, 10, 15, 15, 29),
            attempt_count=1,
            caption_snapshot="Monza podium celebration!",
            media_kind="image",
            title_snapshot="Podium",
            repliz_schedule_id=None,
            facebook_post_id="fb_page_555_444",
            facebook_permalink="https://facebook.com/scuderia/posts/444",
            published_at=datetime(2026, 10, 15, 15, 32),
        )
        db_session.add(delivery)
        db_session.flush()

        ev1 = DeliveryEvent(
            delivery_id=delivery.id,
            event_type="published",
            details={"facebook_post_id": "fb_page_555_444", "method": "phone"},
            created_at=datetime(2026, 10, 15, 15, 32),
        )
        db_session.add(ev1)
        db_session.commit()

        # Query endpoint
        resp = client.get("/social-media/activity")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1

        act = data[0]
        assert act["id"] == delivery.id
        assert act["publish_job_id"] == job.id
        assert act["destination_id"] == dest.id
        assert act["destination_name"] == "Scuderia Page"
        assert act["destination_kind"] == "facebook_page"
        assert act["content_project_id"] == project.id
        assert act["device_id"] == device.id
        assert act["device_name"] == "Realme GT"
        assert act["state"] == "published"
        assert act["intended_method"] == "phone"
        assert act["actual_method"] == "phone"
        assert act["facebook_post_id"] == "fb_page_555_444"
        assert act["facebook_permalink"] == "https://facebook.com/scuderia/posts/444"
        assert act["repliz_schedule_id"] is None
        assert len(act["events"]) == 1
        assert act["events"][0]["event_type"] == "published"
        assert act["events"][0]["details"]["facebook_post_id"] == "fb_page_555_444"

    def test_list_activity_filters(self, client, db_session):
        project = TargetFanpage(name="P_Filter", timezone="Europe/London")
        db_session.add(project)
        db_session.flush()

        dest1 = SocialDestination(
            content_project_id=project.id,
            kind="facebook_page",
            name="D1",
            mode="repliz",
            timezone="Europe/London",
            daily_target=1,
            repliz_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        dest2 = SocialDestination(
            content_project_id=project.id,
            kind="facebook_profile",
            name="D2",
            mode="phone",
            timezone="Europe/London",
            daily_target=1,
            phone_daily_target=1,
            min_gap_minutes=5,
            is_active=True,
        )
        db_session.add_all([dest1, dest2])
        db_session.flush()

        job = PublishJob(fanpage_id=project.id, status=PublishJobStatus.published)
        db_session.add(job)
        db_session.flush()

        deliv_sched = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest1.id,
            state="repliz_scheduled",
            actual_method="repliz",
            repliz_schedule_id="sched_1",
        )
        deliv_uncert = PublishDelivery(
            publish_job_id=job.id,
            destination_id=dest2.id,
            state="uncertain",
            intended_method="phone",
        )
        db_session.add_all([deliv_sched, deliv_uncert])
        db_session.commit()

        # Filter by state=uncertain
        resp = client.get("/social-media/activity?state=uncertain")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["state"] == "uncertain"
        assert items[0]["destination_id"] == dest2.id

        # Filter by destination_id
        resp = client.get(f"/social-media/activity?destination_id={dest1.id}")
        assert resp.status_code == 200
        items = resp.json()
        assert len(items) == 1
        assert items[0]["repliz_schedule_id"] == "sched_1"
