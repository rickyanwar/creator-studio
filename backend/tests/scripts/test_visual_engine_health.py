import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from app.scripts.visual_engine_health import get_health, main
from app.models.ai_copy_events import AICopyEvent
from app.services.codex_budget import CodexUsage

@pytest.fixture
def mock_db():
    return MagicMock()

def make_event(
    id,
    created_at,
    outcome,
    error_message=None,
    context="image_gen",
    final_provider=None,
    fanpage_id=None,
):
    ev = AICopyEvent(
        id=id,
        created_at=created_at,
        context=context,
        outcome=outcome,
        error_message=error_message,
        final_provider=final_provider,
        fanpage_id=fanpage_id,
    )
    return ev

def test_get_health_healthy(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [
        make_event(1, now_naive - timedelta(minutes=10), "success"),
        make_event(2, now_naive - timedelta(minutes=20), "recovered"),
        make_event(3, now_naive - timedelta(minutes=30), "success"),
    ]
    
    mock_query = mock_db.query.return_value
    mock_filter = mock_query.filter.return_value
    mock_order = mock_filter.order_by.return_value
    mock_order.limit.return_value.all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 3
    assert health["failed"] == 0
    assert health["fallback"] == 1
    assert health["consecutive_failures"] == 0
    assert not health["unhealthy"]

def test_get_health_unhealthy_consecutive(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [make_event(i, now_naive - timedelta(minutes=i), "failed", f"err {i}") for i in range(1, 6)]
    events.append(make_event(6, now_naive - timedelta(minutes=6), "success"))
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["consecutive_failures"] == 5
    assert health["unhealthy"]
    assert health["last_error"] == "err 1"

def test_get_health_unhealthy_fallback_rate(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [make_event(i, now_naive - timedelta(minutes=i), "recovered") for i in range(1, 11)]
    events.extend([make_event(i, now_naive - timedelta(minutes=i), "success") for i in range(11, 21)])
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 20
    assert health["fallback"] == 10
    assert health["unhealthy"]

def test_get_health_healthy_fallback_rate_under_20_total(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [make_event(i, now_naive - timedelta(minutes=i), "recovered") for i in range(1, 11)]
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 10
    assert health["fallback"] == 10
    assert not health["unhealthy"] # Must be >= 20 total for fallback threshold

def test_get_health_ignores_old_events(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)
    
    events = [
        make_event(1, now_naive - timedelta(hours=1), "failed", "err"),
        make_event(2, now_naive - timedelta(hours=7), "failed", "err"), # older than 6h
    ]
    
    mock_db.query().filter().order_by().limit().all.return_value = events
    
    health = get_health(mock_db, now=now)
    assert health["total"] == 1
    assert health["consecutive_failures"] == 1
    assert not health["unhealthy"]

@patch("app.scripts.visual_engine_health.SessionLocal")
@patch("app.scripts.visual_engine_health.get_health")
def test_main_success(mock_get_health, mock_session_local, capsys):
    mock_get_health.return_value = {"status": "ok"}
    assert main([]) == 0
    out, _ = capsys.readouterr()
    assert '"status": "ok"' in out

@patch("app.scripts.visual_engine_health.SessionLocal")
def test_main_exception(mock_session_local, capsys):
    mock_session_local.side_effect = Exception("DB error")
    assert main([]) == 2
    out, _ = capsys.readouterr()
    assert '"error": "health_unavailable"' in out


def test_get_health_by_engine(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    events = [
        make_event(1, now_naive - timedelta(minutes=5), "success", final_provider="gptcli"),
        make_event(2, now_naive - timedelta(minutes=10), "recovered", final_provider="gptcli"),
        make_event(3, now_naive - timedelta(minutes=15), "failed", final_provider="gptcli"),
        make_event(4, now_naive - timedelta(minutes=20), "success", final_provider="9router"),
        make_event(5, now_naive - timedelta(minutes=25), "failed", final_provider="9router"),
        make_event(6, now_naive - timedelta(minutes=30), "failed", final_provider=None),
        make_event(7, now_naive - timedelta(minutes=35), "deferred", final_provider="gptcli"),
        make_event(8, now_naive - timedelta(minutes=40), "deferred", final_provider=None),
    ]

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events[:20]

    health = get_health(mock_db, now=now)
    assert health["by_engine"]["gptcli"] == {"success": 1, "recovered": 1, "failed": 1}
    assert health["by_engine"]["9router"] == {"success": 1, "recovered": 0, "failed": 1}
    assert health["by_engine"]["none"] == {"success": 0, "recovered": 0, "failed": 1}
    assert health["by_engine"]["deferred"] == 2


def test_get_health_gptcli_errors(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    events = [
        make_event(1, now_naive - timedelta(minutes=5), "recovered", "plan=overflow; gptcli:quota: exhausted; gptcli:timeout: request timed out"),
        make_event(2, now_naive - timedelta(minutes=10), "failed", "plan=primary; gptcli:quota: exhausted"),
        make_event(3, now_naive - timedelta(minutes=15), "failed", "plain error without gptcli"),
    ]

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events[:20]

    health = get_health(mock_db, now=now)
    assert health["gptcli_errors"] == {"quota": 2, "timeout": 1}


def test_get_health_refusals(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    long_refusal = "Motorcycle crash prompt violates safety policy. " * 15
    events = [
        make_event(
            12,
            now_naive - timedelta(minutes=1),
            "failed",
            f"plan=primary; gptcli:refused: {long_refusal}; chat_url=https://chatgpt.com/c/12",
            fanpage_id=101,
        ),
        make_event(
            11,
            now_naive - timedelta(minutes=2),
            "failed",
            "plan=primary; gptcli:refused: Violence policy violation chat_url=https://chatgpt.com/c/11",
            fanpage_id=102,
        ),
    ]
    for i in range(10, 0, -1):
        events.append(
            make_event(
                i,
                now_naive - timedelta(minutes=20 - i),
                "failed",
                f"plan=primary; gptcli:refused: refusal {i} chat_url=https://chatgpt.com/c/{i}",
                fanpage_id=100 + i,
            )
        )

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events[:20]

    health = get_health(mock_db, now=now)
    refusals = health["refusals"]
    assert len(refusals) == 10
    assert refusals[0]["id"] == 12
    assert refusals[1]["id"] == 11
    assert len(refusals[0]["snippet"]) <= 300
    assert refusals[0]["snippet"].startswith("Motorcycle crash")
    assert refusals[0]["chat_url"] == "https://chatgpt.com/c/12"
    assert refusals[0]["fanpage_id"] == 101
    assert refusals[1]["snippet"] == "Violence policy violation"
    assert refusals[1]["chat_url"] == "https://chatgpt.com/c/11"
    assert refusals[1]["fanpage_id"] == 102


def test_get_health_cloudflare(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    events = [
        make_event(
            2,
            now_naive - timedelta(minutes=5),
            "failed",
            "plan=primary; gptcli:cloudflare_challenge: challenge required; chat_url=https://chatgpt.com/c/cf1",
            fanpage_id=50,
        ),
        make_event(
            1,
            now_naive - timedelta(minutes=10),
            "failed",
            "plan=primary; gptcli:cloudflare_challenge: cloudflare block",
            fanpage_id=51,
        ),
    ]

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events[:20]

    health = get_health(mock_db, now=now)
    cf = health["cloudflare"]
    assert len(cf) == 2
    assert cf[0]["id"] == 2
    assert cf[0]["fanpage_id"] == 50
    assert cf[0]["chat_url"] == "https://chatgpt.com/c/cf1"
    assert "snippet" not in cf[0]
    assert cf[1]["id"] == 1
    assert cf[1]["fanpage_id"] == 51
    assert cf[1]["chat_url"] is None


def test_get_health_identity_rejects(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    events = [
        make_event(1, now_naive - timedelta(minutes=5), "failed", context="image_identity", error_message="sface=0.2 gemini=no"),
        make_event(2, now_naive - timedelta(minutes=10), "failed", context="image_identity", error_message="sface=0.3 gemini=no"),
        make_event(3, now_naive - timedelta(minutes=15), "success", context="image_identity"),
        make_event(4, now_naive - timedelta(minutes=20), "failed", context="image_identity", error_message="sface=0.1 gemini=no"),
        make_event(5, now_naive - timedelta(minutes=25), "failed", context="image_gen", error_message="gen failed"),
    ]

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events[:20]

    health = get_health(mock_db, now=now)
    assert health["identity_rejects"] == 3


@patch("app.scripts.visual_engine_health.get_codex_usage")
def test_get_health_codex_usage_exception(mock_get_codex, mock_db):
    mock_get_codex.side_effect = RuntimeError("redis connection refused")
    now = datetime.now(timezone.utc)

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = []
    mock_order.limit.return_value.all.return_value = []

    health = get_health(mock_db, now=now)
    assert health["codex_usage"] is None


@patch("app.scripts.visual_engine_health.get_codex_usage")
def test_get_health_codex_usage_success(mock_get_codex, mock_db):
    reset_dt = datetime(2026, 10, 17, 18, 0, tzinfo=timezone.utc)
    mock_get_codex.return_value = CodexUsage(
        window_pct=42.5,
        weekly_pct=15.0,
        resets_at=reset_dt,
        source="9router",
    )
    now = datetime.now(timezone.utc)

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = []
    mock_order.limit.return_value.all.return_value = []

    health = get_health(mock_db, now=now)
    assert health["codex_usage"] == {
        "window_pct": 42.5,
        "weekly_pct": 15.0,
        "resets_at": reset_dt.isoformat(),
        "source": "9router",
    }


def test_get_health_consecutive_failures_skips_deferred(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    events = [
        make_event(4, now_naive - timedelta(minutes=1), "failed", "err 4"),
        make_event(3, now_naive - timedelta(minutes=2), "deferred"),
        make_event(2, now_naive - timedelta(minutes=3), "failed", "err 2"),
        make_event(1, now_naive - timedelta(minutes=4), "success"),
    ]

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events

    health = get_health(mock_db, now=now)
    assert health["consecutive_failures"] == 2
    assert health["last_error"] == "err 4"


def test_get_health_legacy_dataset(mock_db):
    now = datetime.now(timezone.utc)
    now_naive = now.replace(tzinfo=None)

    events = [
        make_event(1, now_naive - timedelta(minutes=10), "success", final_provider="9router"),
        make_event(2, now_naive - timedelta(minutes=20), "recovered", final_provider="9router"),
        make_event(3, now_naive - timedelta(minutes=30), "failed", "legacy error msg", final_provider="9router"),
    ]

    mock_order = mock_db.query.return_value.filter.return_value.order_by.return_value
    mock_order.all.return_value = events
    mock_order.limit.return_value.all.return_value = events

    health = get_health(mock_db, now=now)
    assert health["total"] == 3
    assert health["failed"] == 1
    assert health["fallback"] == 1
    assert health["consecutive_failures"] == 0
    assert health["last_error"] == ""
    assert health["unhealthy"] is False
    assert health["by_engine"]["9router"] == {"success": 1, "recovered": 1, "failed": 1}
    assert health["by_engine"]["gptcli"] == {"success": 0, "recovered": 0, "failed": 0}
    assert health["by_engine"]["none"] == {"success": 0, "recovered": 0, "failed": 0}
    assert health["by_engine"]["deferred"] == 0
    assert health["gptcli_errors"] == {}
    assert health["refusals"] == []
    assert health["cloudflare"] == []
    assert health["identity_rejects"] == 0
