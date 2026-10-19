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
    FOLLOW_POLL_S,
    MAX_WAIT_S,
    POST_WAIT_S,
    RUN_LIMIT_S,
    TEMPORARY_TYPES,
    UNAVAILABLE_KEY,
    UNAVAILABLE_REASON_KEY,
    UNAVAILABLE_STREAK_KEY,
    WIB,
    GptcliConfig,
    GptcliError,
    GptcliResult,
    generate_image,
    get_gptcli_config,
    gptcli_applies,
    gptcli_unavailable_until,
    job_status,
    job_timeout_s,
    mark_gptcli_unavailable,
    request_id_for,
    serve_health,
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

    def incr(self, key: str, amount: int = 1) -> int:
        val = int(self._data.get(key, 0)) + amount
        self._data[key] = str(val)
        return val

    def expire(self, key: str, seconds: int):
        self._ttl[key] = seconds
        return True


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
    fake = _FakeRedis()

    with patch("app.services.gptcli_client._redis", return_value=fake):
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
    assert kwargs["timeout"] == httpx.Timeout(POST_WAIT_S, connect=10.0)


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
        with patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timeout")), \
             patch("app.services.gptcli_client.job_status", return_value=None):
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


# ── 12. Timeout configuration (MAX_WAIT_S & max_wait_s keyword) ──────────────

def test_generate_image_timeout_configuration():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp = httpx.Response(200, json={"data": [{"b64_json": b64_str}]})
    fake = _FakeRedis()

    with patch("app.services.gptcli_client._redis", return_value=fake):
        # 1. Initial POST timeout is always POST_WAIT_S (90 s)
        with patch("app.services.gptcli_client.httpx.post", return_value=resp) as mock_post:
            generate_image(cfg, "p", ["img1"], request_id="cs-to-1")
            assert mock_post.call_args.kwargs["timeout"] == httpx.Timeout(POST_WAIT_S, connect=10.0)

        # 2. Re-POST timeout uses MAX_WAIT_S (1800 s) by default
        with patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("slow"), resp]) as mock_post, \
             patch("app.services.gptcli_client.job_status", return_value=None):
            generate_image(cfg, "p", ["img1"], request_id="cs-to-fb-1")
            assert mock_post.call_args_list[0].kwargs["timeout"] == httpx.Timeout(POST_WAIT_S, connect=10.0)
            assert mock_post.call_args_list[1].kwargs["timeout"] == httpx.Timeout(1800, connect=10.0)

        # 3. max_wait_s given and larger than job timeout (2000 > 420): equals max_wait_s
        with patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("slow"), resp]) as mock_post, \
             patch("app.services.gptcli_client.job_status", return_value=None):
            generate_image(cfg, "p", ["img1"], request_id="cs-to-fb-2", max_wait_s=2000.0)
            assert mock_post.call_args_list[1].kwargs["timeout"] == httpx.Timeout(2000.0, connect=10.0)

        # 4. max_wait_s smaller than job timeout for 3 images (job_timeout 600 + 120 = 720, max_wait_s 500): takes job timeout
        with patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("slow"), resp]) as mock_post, \
             patch("app.services.gptcli_client.job_status", return_value=None):
            generate_image(cfg, "p", ["img1", "img2", "img3"], request_id="cs-to-fb-3", max_wait_s=500.0)
            assert mock_post.call_args_list[1].kwargs["timeout"] == httpx.Timeout(720, connect=10.0)


# ── 13. Single "unavailable" does NOT call mark_gptcli_unavailable ───────────

def test_unavailable_single_failure_does_not_mark_unavailable():
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
    resp = httpx.Response(503, json={"error": {"type": "unavailable", "message": "browser closed"}})

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.mark_gptcli_unavailable") as mock_mark:
        with patch("app.services.gptcli_client.httpx.post", return_value=resp):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-unavail-1")
            assert exc_info.value.type == "unavailable"

        mock_mark.assert_not_called()
        assert fake.get(UNAVAILABLE_KEY) is None
        assert fake.get(UNAVAILABLE_STREAK_KEY) == "1"
        assert fake._ttl.get(UNAVAILABLE_STREAK_KEY) == 900


# ── 14. 3rd consecutive "unavailable" DOES call mark_gptcli_unavailable ──────

def test_unavailable_third_consecutive_failure_marks_unavailable():
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
    resp = httpx.Response(503, json={"error": {"type": "unavailable", "message": "browser closed"}})

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.mark_gptcli_unavailable", wraps=mark_gptcli_unavailable) as mock_mark:
        with patch("app.services.gptcli_client.httpx.post", return_value=resp):
            # 1st failure
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-unavail-1")
            assert mock_mark.call_count == 0
            assert fake.get(UNAVAILABLE_KEY) is None
            assert fake.get(UNAVAILABLE_STREAK_KEY) == "1"

            # 2nd failure
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-unavail-2")
            assert mock_mark.call_count == 0
            assert fake.get(UNAVAILABLE_KEY) is None
            assert fake.get(UNAVAILABLE_STREAK_KEY) == "2"

            # 3rd consecutive failure -> triggers cooldown
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-unavail-3")
            assert mock_mark.call_count == 1
            assert fake.get(UNAVAILABLE_KEY) is not None
            assert fake.get(UNAVAILABLE_REASON_KEY) == "unavailable"
            assert fake.get(UNAVAILABLE_STREAK_KEY) == "3"


# ── 15. Success between failures resets the streak ───────────────────────────

def test_success_between_failures_resets_streak():
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
    resp_err = httpx.Response(503, json={"error": {"type": "unavailable", "message": "browser closed"}})
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp_ok = httpx.Response(200, json={"data": [{"b64_json": b64_str}]})

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.mark_gptcli_unavailable") as mock_mark:
        # Failure 1
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_err):
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-streak-1")
        assert fake.get(UNAVAILABLE_STREAK_KEY) == "1"
        assert mock_mark.call_count == 0

        # Success resets streak
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_ok):
            res = generate_image(cfg, "p", [], request_id="cs-streak-ok")
            assert isinstance(res, GptcliResult)
        assert fake.get(UNAVAILABLE_STREAK_KEY) is None

        # Failure 2 (streak restarted from 1)
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_err):
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-streak-2")
        assert fake.get(UNAVAILABLE_STREAK_KEY) == "1"
        assert mock_mark.call_count == 0

        # Failure 3 (streak reaches 2, still no mark)
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_err):
            with pytest.raises(GptcliError):
                generate_image(cfg, "p", [], request_id="cs-streak-3")
        assert fake.get(UNAVAILABLE_STREAK_KEY) == "2"
        assert mock_mark.call_count == 0


# ── 16. "quota" and "logged_out" still mark immediately ──────────────────────

def test_quota_and_logged_out_mark_immediately():
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
        # quota marks immediately
        resp_quota = httpx.Response(429, json={"error": {"type": "quota", "message": "rate limit"}})
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_quota):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-q")
            assert exc_info.value.type == "quota"
        assert fake.get(UNAVAILABLE_KEY) is not None
        assert fake.get(UNAVAILABLE_REASON_KEY) == "quota"
        assert fake.get(UNAVAILABLE_STREAK_KEY) is None

        fake.delete(UNAVAILABLE_KEY, UNAVAILABLE_REASON_KEY)

        # logged_out marks immediately
        resp_logout = httpx.Response(503, json={"error": {"type": "logged_out", "message": "session expired"}})
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_logout):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-lo")
            assert exc_info.value.type == "logged_out"
        assert fake.get(UNAVAILABLE_KEY) is not None
        assert fake.get(UNAVAILABLE_REASON_KEY) == "logged_out"
        assert fake.get(UNAVAILABLE_STREAK_KEY) is None


# ── 17. "timeout" (httpx.TimeoutException) does not mark ─────────────────────

def test_timeout_does_not_mark_unavailable():
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
    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.mark_gptcli_unavailable") as mock_mark:
        with patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timed out")), \
             patch("app.services.gptcli_client.job_status", return_value=None):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-to")
            assert exc_info.value.type == "timeout"
        mock_mark.assert_not_called()
        assert fake.get(UNAVAILABLE_KEY) is None
        assert fake.get(UNAVAILABLE_STREAK_KEY) is None


# ── 18. Redis failure in streak logic does not raise ─────────────────────────

def test_redis_failure_in_streak_logic_does_not_raise():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )

    # 1. Failure tracking Redis error -> raises GptcliError, not RedisError
    raising_redis = MagicMock()
    raising_redis.incr.side_effect = redis.RedisError("redis connection died")
    resp_err = httpx.Response(503, json={"error": {"type": "unavailable", "message": "closed"}})

    with patch("app.services.gptcli_client._redis", return_value=raising_redis):
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_err):
            with pytest.raises(GptcliError) as exc_info:
                generate_image(cfg, "p", [], request_id="cs-redis-fail")
            assert exc_info.value.type == "unavailable"

    # 2. Success delete Redis error -> returns GptcliResult, not RedisError
    raising_delete = MagicMock()
    raising_delete.delete.side_effect = redis.RedisError("redis connection died")
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp_ok = httpx.Response(200, json={"data": [{"b64_json": b64_str}]})

    with patch("app.services.gptcli_client._redis", return_value=raising_delete):
        with patch("app.services.gptcli_client.httpx.post", return_value=resp_ok):
            res = generate_image(cfg, "p", [], request_id="cs-redis-del-fail")
            assert isinstance(res, GptcliResult)
            assert res.png == png_bytes


# ── 19. GC2: job_status and serve_health helpers ────────────────────────────

def test_job_status_success_and_headers():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099/",
        token="tok-123",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    job_payload = {
        "request_id": "cs-test-job",
        "state": "running",
        "queue_position": 0,
        "running_s": 42.5,
    }
    resp = httpx.Response(200, json=job_payload)
    with patch("app.services.gptcli_client.httpx.get", return_value=resp) as mock_get:
        st = job_status(cfg, "cs-test-job")
        assert st == job_payload
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "http://172.17.0.1:8099/v1/jobs/cs-test-job"
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert kwargs["timeout"] == 10.0


def test_job_status_404_and_errors_return_none():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok-123",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    # 404 (e.g. old gpt serve)
    with patch("app.services.gptcli_client.httpx.get", return_value=httpx.Response(404, text="Not Found")):
        assert job_status(cfg, "cs-404") is None

    # 500 error
    with patch("app.services.gptcli_client.httpx.get", return_value=httpx.Response(500, text="Server Error")):
        assert job_status(cfg, "cs-500") is None

    # Network exception
    with patch("app.services.gptcli_client.httpx.get", side_effect=httpx.ConnectError("refused")):
        assert job_status(cfg, "cs-err") is None


def test_serve_health_success_and_headers():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099/",
        token="tok-123",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    health_payload = {
        "ok": True,
        "sleeping": False,
        "queue_len": 1,
        "hb_age_s": 2.1,
        "profiles": {
            "utama": {
                "running": True,
                "running_request_id": "cs-running-1",
                "today_count": 12,
                "soft_cap_daily": 80,
                "quota_until": None,
                "logged_in": True,
            }
        },
    }
    resp = httpx.Response(200, json=health_payload)
    with patch("app.services.gptcli_client.httpx.get", return_value=resp) as mock_get:
        res = serve_health(cfg)
        assert res == health_payload
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "http://172.17.0.1:8099/health"
        assert kwargs["headers"]["Authorization"] == "Bearer tok-123"
        assert kwargs["timeout"] == 10.0


def test_serve_health_errors_return_none():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok-123",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    with patch("app.services.gptcli_client.httpx.get", return_value=httpx.Response(500)):
        assert serve_health(cfg) is None

    with patch("app.services.gptcli_client.httpx.get", side_effect=httpx.ConnectTimeout("timeout")):
        assert serve_health(cfg) is None


# ── 20. GC2: Follow loop in generate_image ──────────────────────────────────

def test_follow_loop_post_returns_in_time_no_polling():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp = httpx.Response(200, json={"data": [{"b64_json": b64_str}]})
    fake = _FakeRedis()

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", return_value=resp) as mock_post, \
         patch("app.services.gptcli_client.job_status") as mock_status:
        res = generate_image(cfg, "prompt", [], request_id="cs-in-time")
        assert isinstance(res, GptcliResult)
        assert res.png == png_bytes
        mock_post.assert_called_once()
        mock_status.assert_not_called()


def test_follow_loop_read_timeout_queued_running_done():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp_done = httpx.Response(200, json={"data": [{"b64_json": b64_str}], "gptcli": {"cached": True}})
    fake = _FakeRedis()

    job_responses = [
        {"request_id": "cs-f1", "state": "queued", "queue_position": 2, "running_s": 0},
        {"request_id": "cs-f1", "state": "running", "queue_position": 0, "running_s": 45},
        {"request_id": "cs-f1", "state": "done", "queue_position": 0, "running_s": 85},
    ]

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("read timeout"), resp_done]) as mock_post, \
         patch("app.services.gptcli_client.job_status", side_effect=job_responses) as mock_status, \
         patch("app.services.gptcli_client.time.sleep") as mock_sleep:
        res = generate_image(cfg, "prompt", [], request_id="cs-f1")
        assert isinstance(res, GptcliResult)
        assert res.png == png_bytes
        assert res.meta.get("cached") is True

        assert mock_post.call_count == 2
        # Initial post had POST_WAIT_S
        assert mock_post.call_args_list[0].kwargs["timeout"] == httpx.Timeout(POST_WAIT_S, connect=10.0)
        # Re-post had normal GC1 timeout
        assert mock_post.call_args_list[1].kwargs["timeout"] == httpx.Timeout(MAX_WAIT_S, connect=10.0)
        # Follow polled 3 times
        assert mock_status.call_count == 3
        # Slept twice between polls
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(FOLLOW_POLL_S)


def test_follow_loop_failed_reposts_and_handles_error():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    resp_failed = httpx.Response(
        502,
        json={"error": {"type": "refused", "message": "content policy violation"}},
    )
    fake = _FakeRedis()

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("read timeout"), resp_failed]) as mock_post, \
         patch("app.services.gptcli_client.job_status", return_value={"state": "failed", "queue_position": 0}):
        with pytest.raises(GptcliError) as exc_info:
            generate_image(cfg, "prompt", [], request_id="cs-fail-1")
        assert exc_info.value.type == "refused"
        assert exc_info.value.status == 502
        assert mock_post.call_count == 2


def test_follow_loop_unknown_twice_timeout():
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

    job_responses = [
        {"request_id": "cs-unk", "state": "unknown"},
        {"request_id": "cs-unk", "state": "unknown"},
    ]

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timeout")), \
         patch("app.services.gptcli_client.job_status", side_effect=job_responses), \
         patch("app.services.gptcli_client.time.sleep") as mock_sleep:
        with pytest.raises(GptcliError) as exc_info:
            generate_image(cfg, "prompt", [], request_id="cs-unk")
        assert exc_info.value.type == "timeout"
        assert "gpt serve lost the job" in exc_info.value.message
        assert mock_sleep.call_count == 1


def test_follow_loop_none_four_times_timeout():
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

    # First call returns a valid dict (so not old gpt serve fallback), followed by 4 None responses
    job_responses = [
        {"request_id": "cs-n4", "state": "queued"},
        None,
        None,
        None,
        None,
    ]

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timeout")), \
         patch("app.services.gptcli_client.job_status", side_effect=job_responses), \
         patch("app.services.gptcli_client.time.sleep"):
        with pytest.raises(GptcliError) as exc_info:
            generate_image(cfg, "prompt", [], request_id="cs-n4")
        assert exc_info.value.type == "timeout"
        assert "gpt serve lost the job" in exc_info.value.message


def test_follow_loop_running_s_exceeds_600s_timeout():
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

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timeout")), \
         patch("app.services.gptcli_client.job_status", return_value={"state": "running", "running_s": 601}):
        with pytest.raises(GptcliError) as exc_info:
            generate_image(cfg, "prompt", [], request_id="cs-r600")
        assert exc_info.value.type == "timeout"
        assert "job ran longer than 600 s" in exc_info.value.message


def test_follow_loop_total_wait_exceeds_max_wait_s():
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

    # Simulate time advancing beyond MAX_WAIT_S (1800)
    monotonic_vals = [0.0, 1801.0]

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=httpx.ReadTimeout("read timeout")), \
         patch("app.services.gptcli_client.time.monotonic", side_effect=monotonic_vals):
        with pytest.raises(GptcliError) as exc_info:
            generate_image(cfg, "prompt", [], request_id="cs-waited")
        assert exc_info.value.type == "timeout"
        assert "waited 1800 s" in exc_info.value.message


def test_follow_loop_old_gpt_serve_fallback():
    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token="tok",
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp_fallback_ok = httpx.Response(200, json={"data": [{"b64_json": b64_str}], "gptcli": {"old": True}})
    fake = _FakeRedis()

    with patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("read timeout"), resp_fallback_ok]) as mock_post, \
         patch("app.services.gptcli_client.job_status", return_value=None) as mock_status:
        res = generate_image(cfg, "prompt", [], request_id="cs-old")
        assert isinstance(res, GptcliResult)
        assert res.png == png_bytes
        # 1st call to status was None -> triggered fallback
        assert mock_status.call_count == 1
        assert mock_post.call_count == 2
        assert mock_post.call_args_list[1].kwargs["timeout"] == httpx.Timeout(MAX_WAIT_S, connect=10.0)


def test_follow_loop_no_prompt_or_token_in_logs(caplog):
    import logging

    secret_prompt = "TOP_SECRET_PROMPT_MOTORCYCLE_CRASH"
    secret_token = "SUPER_SECRET_GPTCLI_TOKEN_999"

    cfg = GptcliConfig(
        enabled=True,
        base_url="http://172.17.0.1:8099",
        token=secret_token,
        fanpage_ids=(),
        thread="studio",
        size="1080x1350",
        day_start="05:30",
    )
    png_bytes = _make_png_bytes(8, 8)
    b64_str = base64.b64encode(png_bytes).decode("ascii")
    resp_ok = httpx.Response(200, json={"data": [{"b64_json": b64_str}]})
    fake = _FakeRedis()

    job_responses = [
        {"request_id": "cs-log-1", "state": "queued", "queue_position": 1, "running_s": 0},
        {"request_id": "cs-log-1", "state": "done", "queue_position": 0, "running_s": 30},
    ]

    with caplog.at_level(logging.INFO, logger="app.services.gptcli_client"), \
         patch("app.services.gptcli_client._redis", return_value=fake), \
         patch("app.services.gptcli_client.httpx.post", side_effect=[httpx.ReadTimeout("read timeout"), resp_ok]), \
         patch("app.services.gptcli_client.job_status", side_effect=job_responses), \
         patch("app.services.gptcli_client.time.sleep"):
        res = generate_image(cfg, secret_prompt, [], request_id="cs-log-1")
        assert isinstance(res, GptcliResult)

    all_logs = caplog.text
    assert secret_prompt not in all_logs
    assert secret_token not in all_logs
    assert "gptcli: job cs-log-1 queued" in all_logs
