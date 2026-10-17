from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user, get_db
from app.api.settings import ai_engine_status, router
from app.main import app
from app.models.ai_copy_events import AICopyEvent
from app.models.settings import Settings
from app.services.codex_budget import CodexUsage
from app.services.engine_router import WIB
from app.services.gptcli_client import GptcliConfig


from sqlalchemy.pool import StaticPool


@pytest.fixture
def status_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    AICopyEvent.__table__.create(engine)
    Settings.__table__.create(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    row = Settings(id=1, codex_share_window_jobs=40)
    session.add(row)
    session.commit()
    yield session
    session.close()


def test_route_registration():
    """Verify route is registered as /settings/ai-engine-status on app.api.settings.router."""
    assert any(r.path == "/settings/ai-engine-status" and "GET" in r.methods for r in router.routes)


def test_ai_engine_status_counts_and_isolation(status_db):
    """Counts per engine/outcome, deferred counted separately, identity_rejects counted."""
    now_utc = datetime.now(timezone.utc)
    now_wib = now_utc.astimezone(WIB)
    wib_midnight = datetime(now_wib.year, now_wib.month, now_wib.day, 0, 0, 0, tzinfo=WIB)
    utc_start = wib_midnight.astimezone(timezone.utc).replace(tzinfo=None)

    # Within today
    events = [
        AICopyEvent(context="image_gen", outcome="success", final_provider="gptcli", created_at=utc_start + timedelta(minutes=10)),
        AICopyEvent(context="image_gen", outcome="recovered", final_provider="gptcli", created_at=utc_start + timedelta(minutes=20)),
        AICopyEvent(context="image_gen", outcome="failed", final_provider="gptcli", created_at=utc_start + timedelta(minutes=30)),
        AICopyEvent(context="image_gen", outcome="success", final_provider="9router", created_at=utc_start + timedelta(minutes=40)),
        AICopyEvent(context="image_gen", outcome="failed", final_provider="9router", created_at=utc_start + timedelta(minutes=50)),
        AICopyEvent(context="image_gen", outcome="failed", final_provider=None, created_at=utc_start + timedelta(minutes=60)),
        # deferred outcome: provider set to gptcli, but must only be counted under 'deferred'
        AICopyEvent(context="image_gen", outcome="deferred", final_provider="gptcli", created_at=utc_start + timedelta(minutes=70)),
        # identity rejects
        AICopyEvent(context="image_identity", outcome="failed", created_at=utc_start + timedelta(minutes=80)),
        AICopyEvent(context="image_identity", outcome="success", created_at=utc_start + timedelta(minutes=90)),
        # yesterday (before today's WIB start) - should not be counted
        AICopyEvent(context="image_gen", outcome="success", final_provider="gptcli", created_at=utc_start - timedelta(hours=2)),
        AICopyEvent(context="image_identity", outcome="failed", created_at=utc_start - timedelta(hours=2)),
    ]
    for ev in events:
        status_db.add(ev)
    status_db.commit()

    mock_cfg = GptcliConfig(
        enabled=True,
        base_url="http://localhost:8099",
        token="secret-never-expose-token-12345",
        fanpage_ids=(10, 20),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    mock_usage = CodexUsage(
        window_pct=42.5,
        weekly_pct=28.0,
        resets_at=datetime(2026, 10, 18, 5, 30, tzinfo=timezone.utc),
        source="estimate",
    )

    unavail_dt = datetime(2026, 10, 17, 14, 0, tzinfo=timezone.utc)
    mock_redis = MagicMock()
    mock_redis.get.return_value = "quota"

    with patch("app.services.gptcli_client.get_gptcli_config", return_value=mock_cfg), \
         patch("app.services.codex_budget.get_codex_usage", return_value=mock_usage), \
         patch("app.services.engine_router.codex_share", return_value=0.352), \
         patch("app.services.engine_router.visual_queue_len", return_value=3), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=unavail_dt), \
         patch("app.services.gptcli_client._redis", return_value=mock_redis):

        res = ai_engine_status(db=status_db, _=None)

    # 1. Counts
    today = res["today"]
    assert today["gptcli"]["success"] == 1
    assert today["gptcli"]["recovered"] == 1
    assert today["gptcli"]["failed"] == 1
    assert today["9router"]["success"] == 1
    assert today["9router"]["recovered"] == 0
    assert today["9router"]["failed"] == 1
    assert today["none"]["failed"] == 1
    assert today["deferred"] == 1

    # 2. Identity rejects
    assert res["identity_rejects_today"] == 1

    # 3. Codex day share
    assert res["codex_day_share"] == 35.2

    # 4. Codex usage
    assert res["codex_usage"] == {
        "window_pct": 42.5,
        "weekly_pct": 28.0,
        "resets_at": mock_usage.resets_at.isoformat(),
        "source": "estimate",
    }

    # 5. gptcli unavailable
    assert res["gptcli_unavailable_until"] == unavail_dt.isoformat()
    assert res["gptcli_unavailable_reason"] == "quota"

    # 6. visual queue len
    assert res["visual_queue_len"] == 3

    # 7. gptcli config fields
    assert res["gptcli_enabled"] is True
    assert res["gptcli_fanpage_ids"] == [10, 20]

    # 8. Token must never be present
    assert "token" not in res
    assert "secret-never-expose-token-12345" not in str(res)


def test_codex_usage_none_when_raises(status_db):
    """When get_codex_usage raises, codex_usage in status is None (not 500)."""
    with patch("app.services.codex_budget.get_codex_usage", side_effect=RuntimeError("usage timeout")), \
         patch("app.services.gptcli_client.get_gptcli_config", return_value=GptcliConfig(False, "", "", (), "", "", "05:30")), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.visual_queue_len", return_value=0):

        res = ai_engine_status(db=status_db, _=None)

    assert res["codex_usage"] is None
    assert res["gptcli_unavailable_until"] is None
    assert res["gptcli_unavailable_reason"] is None


def test_no_token_in_response(status_db):
    """Response dict never includes token or auth secrets."""
    secret = "sk-super-secret-key-xyz"
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token=secret,
        fanpage_ids=(1,),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    with patch("app.services.gptcli_client.get_gptcli_config", return_value=cfg), \
         patch("app.services.codex_budget.get_codex_usage", return_value=CodexUsage(10.0, None, None, "estimate")), \
         patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=None), \
         patch("app.services.engine_router.visual_queue_len", return_value=0):

        res = ai_engine_status(db=status_db, _=None)

    assert secret not in str(res)
    assert "token" not in res
    assert "gptcli_token" not in res


def test_testclient_endpoint_access(status_db):
    """FastAPI TestClient hits /settings/ai-engine-status and receives 200 JSON."""
    app.dependency_overrides[get_db] = lambda: status_db
    app.dependency_overrides[get_current_user] = lambda: "testuser"
    client = TestClient(app)

    try:
        with patch("app.services.gptcli_client.get_gptcli_config", return_value=GptcliConfig(False, "", "", (), "", "", "05:30")), \
             patch("app.services.codex_budget.get_codex_usage", return_value=CodexUsage(0.0, None, None, "estimate")), \
             patch("app.services.gptcli_client.gptcli_unavailable_until", return_value=None), \
             patch("app.services.engine_router.visual_queue_len", return_value=0):

            res = client.get("/settings/ai-engine-status")
            assert res.status_code == 200
            data = res.json()
            assert "day_start_wib" in data
            assert "today" in data
            assert "identity_rejects_today" in data
            assert "codex_day_share" in data
            assert "codex_usage" in data
            assert "gptcli_unavailable_until" in data
            assert "gptcli_unavailable_reason" in data
            assert "visual_queue_len" in data
            assert "gptcli_enabled" in data
            assert "gptcli_fanpage_ids" in data
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)
