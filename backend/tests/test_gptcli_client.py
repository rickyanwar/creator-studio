"""Tests for backend/app/services/gptcli_client.py.

Rules enforced:
- No real network calls (httpx.post mocked).
- In-memory Redis stub (no fakeredis dependency).
- Real 8x8 PNG generated with PIL for success tests.
"""

import base64
from datetime import datetime, timedelta, timezone
import io
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import httpx
from PIL import Image
import pytest
import redis

from app.models.settings import Settings
from app.services.gptcli_client import (
    DEFAULT_UNAVAILABLE_S,
    TEMPORARY_TYPES,
    UNAVAILABLE_KEY,
    UNAVAILABLE_REASON_KEY,
    WIB,
    GptcliConfig,
    GptcliError,
    GptcliResult,
    generate_image,
    get_gptcli_config,
    gptcli_applies,
    gptcli_unavailable_until,
    job_timeout_s,
    mark_gptcli_unavailable,
    request_id_for,
)


class _FakeRedis:
    """Minimal Redis in-memory stub for testing."""

    def __init__(self):
        self._data: dict[str, str] = {}
        self._ttl: dict[str, float] = {}

    def get(self, key: str):
        return self._data.get(key)

    def set(self, key: str, value, ex=None):
        self._data[key] = str(value)
        if ex is not None:
            self._ttl[key] = ex

    def delete(self, *keys: str):
        for k in keys:
            self._data.pop(k, None)
            self._ttl.pop(k, None)


def _make_png_bytes(width: int = 8, height: int = 8, color: tuple = (255, 0, 0)) -> bytes:
    im = Image.new("RGB", (width, height), color)
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


# ── 1. Success decodes PNG and returns meta ──────────────────────────────────

def test_generate_image_success_decodes_png_and_returns_meta():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099/",
        token="test-secret-token",
        fanpage_ids=(1,),
        thread="studio-thread",
        size="1080x1350",
        day_start="05:30",
    )
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    meta = {
        "request_id": "cs-test-123",
        "duration_s": 12.3,
        "chat_url": "https://chatgpt.com/c/abc",
    }
    resp = httpx.Response(200, json={"data": [{"b64_json": b64_str}], "gptcli": meta})

    with patch("app.services.gptcli_client.httpx.post", return_value=resp) as mock_post:
        result = generate_image(
            cfg,
            prompt="A racing bike",
            images=["data:image/jpeg;base64,aaa"],
            request_id="cs-req-001",
        )

    assert isinstance(result, GptcliResult)
    assert result.png == png_bytes
    assert result.meta == meta

    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert args[0] == "http://172.17.0.1:8099/v1/images/generations"
    assert kwargs["json"] == {
        "prompt": "A racing bike",
        "images": ["data:image/jpeg;base64,aaa"],
        "size": "1080x1350",
        "thread": "studio-thread",
    }
    assert kwargs["headers"]["Authorization"] == "Bearer test-secret-token"
    assert kwargs["headers"]["X-Request-Id"] == "cs-req-001"
    assert kwargs["headers"]["Content-Type"] == "application/json"
    assert kwargs["timeout"] == 300 + 120  # job_timeout_s(1) + HTTP_MARGIN_S


# ── 2. Error types from Section 3 ───────────────────────────────────────────

@pytest.mark.parametrize(
    "status_code,error_type,message,extra_in,expected_extra",
    [
        (400, "bad_request", "invalid request format", {}, {}),
        (401, "unauthorized", "unauthorized access", {}, {}),
        (403, "cloudflare_challenge", "cloudflare challenge required", {}, {}),
        (404, "not_found", "endpoint not found", {}, {}),
        (429, "quota", "plus quota exceeded", {}, {}),
        (429, "soft_cap", "soft cap reached", {}, {}),
        (
            502,
            "refused",
            "I cannot generate images with real people in dangerous situations like racing crashes.",
            {"chat_url": "https://chatgpt.com/c/123", "prompt_head": "Marc Marquez crash..."},
            {"chat_url": "https://chatgpt.com/c/123", "prompt_head": "Marc Marquez crash..."},
        ),
        (502, "no_image", "failed to output image after 2 turns", {}, {}),
        (503, "sleeping", "service in sleeping window", {}, {}),
        (503, "unavailable", "proxy phone unreachable", {}, {}),
        (503, "logged_out", "session expired", {}, {}),
        (503, "busy", "queue length exceeds 20", {}, {}),
        (504, "timeout", "job timeout exceeded", {}, {}),
    ],
)
def test_generate_image_error_types_mapping(status_code, error_type, message, extra_in, expected_extra):
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    fake = _FakeRedis()
    error_payload = {"type": error_type, "message": message}
    error_payload.update(extra_in)
    resp = httpx.Response(status_code, json={"error": error_payload})

    with patch("app.services.gptcli_client._redis", return_value=fake):
        with patch("app.services.gptcli_client.httpx.post", return_value=resp):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "test prompt", [], request_id="cs-err")

            err = exc_info.value
            assert err.type == error_type
            assert err.status == status_code
            assert err.message == message
            assert err.extra == expected_extra


# ── 3. Retry-After header parsed ─────────────────────────────────────────────

def test_generate_image_retry_after_header_parsed():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    fake = _FakeRedis()
    with patch("app.services.gptcli_client._redis", return_value=fake):
        # Valid integer Retry-After
        resp_valid = httpx.Response(
            429,
            json={"error": {"type": "quota", "message": "quota limit"}},
            headers={"Retry-After": "3600"},
        )
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_valid):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-ra-1")
            assert exc_info.value.retry_after_s == 3600

        # Non-integer Retry-After
        resp_invalid = httpx.Response(
            429,
            json={"error": {"type": "quota", "message": "quota limit"}},
            headers={"Retry-After": "not-a-number"},
        )
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_invalid):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-ra-2")
            assert exc_info.value.retry_after_s is None


# ── 4. Transport exceptions mapped ──────────────────────────────────────────

def test_generate_image_transport_exceptions_mapped():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    fake = _FakeRedis()
    with patch("app.services.gptcli_client._redis", return_value=fake):
        # ConnectError -> unreachable
        with patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ConnectError("refused")):
            with pytest.raises(GptcliError) as exc:
                generate_image(cfg, "p", [], request_id="cs-conn")
            assert exc.value.type == "unreachable"

        # ConnectTimeout -> unreachable
        with patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ConnectTimeout("conn timeout")):
            with pytest.raises(GptcliError) as exc:
                generate_image(cfg, "p", [], request_id="cs-connto")
            assert exc.value.type == "unreachable"

        # ReadTimeout -> timeout
        with patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timeout")):
            with pytest.raises(GptcliError) as exc:
                generate_image(cfg, "p", [], request_id="cs-readto")
            assert exc.value.type == "timeout"


# ── 5. 200 with non-image b64 -> bad_response ────────────────────────────────

def test_generate_image_200_invalid_b64_raises_bad_response():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    corrupted_b64 = base64.b64encode(b"not an image").decode("ascii")
    resp = httpx.Response(200, json={"data": [{"b64_json": corrupted_b64}]})
    with patch("app.services.gptcli_client.httpx.post", return_value=resp):
        with pytest.raises(GptcliError) as exc:
            generate_image(cfg, "p", [], request_id="cs-bad")
        assert exc.value.type == "bad_response"
        assert exc.value.status == 200


# ── 6. Temporary types set Redis key; non-temporary do not ──────────────────

def test_temporary_types_set_redis_non_temporary_do_not():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    fake = _FakeRedis()
    with patch("app.services.gptcli_client._redis", return_value=fake):
        # 1. sleeping -> until next day_start
        resp_sleep = httpx.Response(503, json={"error": {"type": "sleeping", "message": "sleep"}})
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_sleep):
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-sleep")
            assert UNAVAILABLE_KEY in fake._data
            assert fake._data[UNAVAILABLE_REASON_KEY] == "sleeping"
            until = gptcli_unavailable_until()
            assert until is not None

        fake.delete(UNAVAILABLE_KEY, UNAVAILABLE_REASON_KEY)

        # 2. quota with Retry-After 3600 -> ~1 h
        resp_quota = httpx.Response(
            429,
            json={"error": {"type": "quota", "message": "quota"}},
            headers={"Retry-After": "3600"},
        )
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_quota):
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-quota")
            assert UNAVAILABLE_KEY in fake._data
            assert fake._data[UNAVAILABLE_REASON_KEY] == "quota"
            until_ts = float(fake._data[UNAVAILABLE_KEY])
            now_ts = datetime.now(timezone.utc).timestamp()
            assert 3500 <= (until_ts - now_ts) <= 3700

        # 3. Non-temporary (refused, bad_request, busy, timeout) do NOT set it
        non_temp_cases = [
            (502, "refused"),
            (400, "bad_request"),
            (503, "busy"),
            (504, "timeout"),
        ]
        for status, err_type in non_temp_cases:
            fake.delete(UNAVAILABLE_KEY, UNAVAILABLE_REASON_KEY)
            resp_non_temp = httpx.Response(
                status,
                json={"error": {"type": err_type, "message": f"{err_type} error"}},
            )
            with patch("app.services.gptcli_client.httpx.post", return_value=resp_non_temp):
                with pytest.raises(GptcliError):
                    generate_image(cfg, "p", [], request_id=f"cs-{err_type}")
                assert UNAVAILABLE_KEY not in fake._data
                assert UNAVAILABLE_REASON_KEY not in fake._data


# ── 7. gptcli_unavailable_until past or Redis raises ────────────────────────

def test_gptcli_unavailable_until_past_or_redis_raises():
    fake = _FakeRedis()
    with patch("app.services.gptcli_client._redis", return_value=fake):
        # In the past
        past_time = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        fake.set(UNAVAILABLE_KEY, str(past_time.timestamp()))
        now = datetime(2026, 1, 1, 13, 0, tzinfo=timezone.utc)
        assert gptcli_unavailable_until(now) is None

        # In the future
        future_time = datetime(2026, 1, 1, 14, 0, tzinfo=timezone.utc)
        fake.set(UNAVAILABLE_KEY, str(future_time.timestamp()))
        assert gptcli_unavailable_until(now) == future_time

        # Missing key
        fake.delete(UNAVAILABLE_KEY)
        assert gptcli_unavailable_until(now) is None

    # Redis raises
    raising_redis = MagicMock()
    raising_redis.get.side_effect = redis.RedisError("connection lost")
    with patch("app.services.gptcli_client._redis", return_value=raising_redis):
        assert gptcli_unavailable_until(now) is None


# ── 8. request_id_for stability & job_timeout_s values ───────────────────────

def test_request_id_for_stability_and_job_timeouts():
    id1 = request_id_for("draw motogp bike", ["img_data_1", "img_data_2"])
    id2 = request_id_for("draw motogp bike", ["img_data_1", "img_data_2"])
    assert id1 == id2
    assert id1.startswith("cs-")
    assert len(id1) == 27

    id_diff_img = request_id_for("draw motogp bike", ["img_data_1", "img_data_diff"])
    assert id1 != id_diff_img

    id_diff_prompt = request_id_for("draw f1 car", ["img_data_1", "img_data_2"])
    assert id1 != id_diff_prompt

    # job_timeout_s
    assert job_timeout_s(0) == 120
    assert job_timeout_s(-1) == 120
    assert job_timeout_s(1) == 300
    assert job_timeout_s(2) == 300
    assert job_timeout_s(3) == 600
    assert job_timeout_s(10) == 600


# ── 9. gptcli_applies variations ─────────────────────────────────────────────

def test_gptcli_applies_matrix():
    # disabled
    cfg_disabled = GptcliConfig(
        enabled=False,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(1,),
        thread="s",
        size="1080x1350",
        day_start="05:30",
    )
    assert not gptcli_applies(cfg_disabled, 1)

    # no token
    cfg_no_tok = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="",
        fanpage_ids=(1,),
        thread="s",
        size="1080x1350",
        day_start="05:30",
    )
    assert not gptcli_applies(cfg_no_tok, 1)

    # empty list -> all fanpages
    cfg_empty = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="s",
        size="1080x1350",
        day_start="05:30",
    )
    assert gptcli_applies(cfg_empty, 1)
    assert gptcli_applies(cfg_empty, None)

    # fanpage in list vs not in list vs None
    cfg_ids = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(10, 20),
        thread="s",
        size="1080x1350",
        day_start="05:30",
    )
    assert gptcli_applies(cfg_ids, 10)
    assert gptcli_applies(cfg_ids, 20)
    assert not gptcli_applies(cfg_ids, 30)
    assert not gptcli_applies(cfg_ids, None)


# ── 10. get_gptcli_config mappings & decrypt failure ─────────────────────────

def test_get_gptcli_config_behavior():
    # 1. db returns None
    db_none = MagicMock()
    db_none.query.return_value.first.return_value = None
    cfg_none = get_gptcli_config(db_none)
    assert cfg_none.enabled is False
    assert cfg_none.base_url == "http://172.17.0.1:8099"
    assert cfg_none.token == ""
    assert cfg_none.fanpage_ids == ()

    # 2. Settings() without new attributes set
    db_plain = MagicMock()
    db_plain.query.return_value.first.return_value = Settings()
    cfg_plain = get_gptcli_config(db_plain)
    assert cfg_plain.enabled is False
    assert cfg_plain.base_url == "http://172.17.0.1:8099"
    assert cfg_plain.token == ""
    assert cfg_plain.fanpage_ids == ()
    assert cfg_plain.thread == "studio"
    assert cfg_plain.size == "1080x1350"
    assert cfg_plain.day_start == "05:30"

    # 3. With values set, decrypted token
    row = MagicMock()
    row.gptcli_enabled = True
    row.gptcli_base_url = "http://my-host:8099/"
    row.gptcli_token_encrypted = "ciphertext"
    row.gptcli_fanpage_ids = [1, 2, "ignore_me", False]
    row.gptcli_thread = "custom_thread"
    row.gptcli_size = "1024x1024"
    row.ai_day_start = "06:00"

    db_val = MagicMock()
    db_val.query.return_value.first.return_value = row

    with patch("app.services.gptcli_client.decrypt", return_value="plain-secret"):
        cfg_val = get_gptcli_config(db_val)
        assert cfg_val.enabled is True
        assert cfg_val.base_url == "http://my-host:8099"
        assert cfg_val.token == "plain-secret"
        assert cfg_val.fanpage_ids == (1, 2)
        assert cfg_val.thread == "custom_thread"
        assert cfg_val.size == "1024x1024"
        assert cfg_val.day_start == "06:00"

    # 4. Decrypt failure -> token ""
    with patch("app.services.gptcli_client.decrypt", side_effect=ValueError("bad key")):
        cfg_fail = get_gptcli_config(db_val)
        assert cfg_fail.token == ""


# ── 11. Token never appears in error message ─────────────────────────────────

def test_token_never_appears_in_error_message():
    secret_token = "TOP_SECRET_GPTCLI_KEY_999"
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token=secret_token,
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    server_err_msg = f"Unauthorized: token {secret_token} is invalid or expired."
    resp = httpx.Response(
        401,
        json={"error": {"type": "unauthorized", "message": server_err_msg}},
    )
    with patch("app.services.gptcli_client.httpx.post", return_value=resp):
        with pytest.raises(GptcliError) as exc_info:
            generate_image(cfg, "prompt", [], request_id="cs-leak-test")

        err = exc_info.value
        assert secret_token not in err.message
        assert secret_token not in str(err)
        assert "***" in err.message


# ── Helper calculation & error handling tests ────────────────────────────────

def test_mark_gptcli_unavailable_sleeping_calculation():
    # 01:00 WIB (18:00 UTC previous day) -> next 05:30 WIB is today
    now_utc = datetime(2026, 10, 16, 18, 0, tzinfo=timezone.utc)
    until = mark_gptcli_unavailable("sleeping", None, now=now_utc, day_start="05:30")
    until_wib = until.astimezone(WIB)
    assert until_wib.hour == 5
    assert until_wib.minute == 30
    assert until_wib.day == 17

    # 06:00 WIB (23:00 UTC previous day) -> next 05:30 WIB is tomorrow
    now_utc_late = datetime(2026, 10, 16, 23, 0, tzinfo=timezone.utc)
    until_late = mark_gptcli_unavailable("sleeping", None, now=now_utc_late, day_start="05:30")
    until_late_wib = until_late.astimezone(WIB)
    assert until_late_wib.hour == 5
    assert until_late_wib.minute == 30
    assert until_late_wib.day == 18


def test_mark_gptcli_unavailable_redis_error_returns_datetime():
    raising_redis = MagicMock()
    raising_redis.set.side_effect = redis.RedisError("redis down")
    now_utc = datetime(2026, 10, 17, 10, 0, tzinfo=timezone.utc)
    with patch("app.services.gptcli_client._redis", return_value=raising_redis):
        until = mark_gptcli_unavailable("quota", 3600, now=now_utc, day_start="05:30")
        assert until == now_utc + timedelta(seconds=3600)
