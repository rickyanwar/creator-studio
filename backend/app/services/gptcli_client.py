"""gpt serve client (owner plan 2026-10-17): ChatGPT web (Plus account) driven by gpt-cli on the VPS,
exposed as an OpenAI-Images-shaped API. Primary AI image engine; Codex via 9Router is the fallback."""

import base64
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import io
import logging
import math
from zoneinfo import ZoneInfo

import httpx
from PIL import Image
import redis

from app.config import get_settings
from app.models.settings import Settings
from app.services.encryption import decrypt

logger = logging.getLogger(__name__)

WIB = ZoneInfo("Asia/Jakarta")
UNAVAILABLE_KEY = "gptcli:unavailable_until"      # epoch seconds (float, as string)
UNAVAILABLE_REASON_KEY = "gptcli:unavailable_reason"
TEMPORARY_TYPES = frozenset({
    "sleeping",
    "quota",
    "soft_cap",
    "logged_out",
    "unavailable",
    "cloudflare_challenge",
    "unreachable",
})
DEFAULT_UNAVAILABLE_S = {
    "quota": 10800,
    "soft_cap": 3600,
    "logged_out": 1800,
    "cloudflare_challenge": 1800,
    "unavailable": 300,
    "unreachable": 300,
}
HTTP_MARGIN_S = 120


@dataclass(frozen=True)
class GptcliConfig:
    enabled: bool
    base_url: str
    token: str
    fanpage_ids: tuple[int, ...]
    thread: str
    size: str
    day_start: str   # "HH:MM" WIB


class GptcliError(Exception):
    def __init__(
        self,
        type_: str,
        message: str,
        *,
        status: int | None = None,
        retry_after_s: int | None = None,
        extra: dict | None = None,
    ):
        super().__init__(message)
        self.type = type_
        self.message = message
        self.status = status
        self.retry_after_s = retry_after_s
        self.extra = dict(extra or {})


@dataclass(frozen=True)
class GptcliResult:
    png: bytes
    meta: dict      # the response's "gptcli" block as-is


def _redis():
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def _sanitize(msg: str, token: str) -> str:
    if token and token in msg:
        return msg.replace(token, "***")
    return msg


def get_gptcli_config(db) -> GptcliConfig:
    row = db.query(Settings).first() if db is not None else None
    if row is None:
        return GptcliConfig(
            enabled=False,
            base_url="http://172.17.0.1:8099",
            token="",
            fanpage_ids=(),
            thread="studio",
            size="1080x1350",
            day_start="05:30",
        )

    enabled = getattr(row, "gptcli_enabled", False) is True

    raw_base_url = getattr(row, "gptcli_base_url", "http://172.17.0.1:8099")
    base_url = str(raw_base_url or "http://172.17.0.1:8099").rstrip("/")

    token_enc = getattr(row, "gptcli_token_encrypted", None)
    token = ""
    if token_enc:
        try:
            token = decrypt(token_enc)
        except Exception as e:
            logger.warning("Failed to decrypt gptcli_token_encrypted: %s", type(e).__name__)
            token = ""

    raw_fanpage_ids = getattr(row, "gptcli_fanpage_ids", [])
    if isinstance(raw_fanpage_ids, (list, tuple, set)):
        fanpage_ids = tuple(
            x for x in raw_fanpage_ids
            if isinstance(x, int) and not isinstance(x, bool)
        )
    else:
        fanpage_ids = ()

    thread = str(getattr(row, "gptcli_thread", "studio") or "studio")
    size = str(getattr(row, "gptcli_size", "1080x1350") or "1080x1350")
    day_start = str(getattr(row, "ai_day_start", "05:30") or "05:30")

    return GptcliConfig(
        enabled=enabled,
        base_url=base_url,
        token=token,
        fanpage_ids=fanpage_ids,
        thread=thread,
        size=size,
        day_start=day_start,
    )


def gptcli_applies(cfg: GptcliConfig, fanpage_id: int | None) -> bool:
    return bool(
        cfg.enabled
        and cfg.token
        and cfg.base_url
        and (not cfg.fanpage_ids or (fanpage_id is not None and fanpage_id in cfg.fanpage_ids))
    )


def request_id_for(prompt: str, images: list[str]) -> str:
    payload = (prompt + "\n" + "\n".join(images)).encode("utf-8")
    return "cs-" + hashlib.sha256(payload).hexdigest()[:24]


def job_timeout_s(n_images: int) -> int:
    if n_images <= 0:
        return 120
    if n_images in (1, 2):
        return 300
    return 600


def gptcli_unavailable_until(now: datetime | None = None) -> datetime | None:
    try:
        r = _redis()
        val = r.get(UNAVAILABLE_KEY)
        if val is None:
            return None
        until_ts = float(val)
        ref_now = now or datetime.now(timezone.utc)
        if ref_now.tzinfo is None:
            ref_now = ref_now.replace(tzinfo=timezone.utc)
        if until_ts > ref_now.timestamp():
            return datetime.fromtimestamp(until_ts, tz=timezone.utc)
        return None
    except Exception as e:
        logger.warning("Redis error in gptcli_unavailable_until: %s", e)
        return None


def mark_gptcli_unavailable(
    type_: str,
    retry_after_s: int | None,
    *,
    now: datetime,
    day_start: str,
) -> datetime:
    ref_now = now
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)
    ref_now_utc = ref_now.astimezone(timezone.utc)

    if retry_after_s is not None and retry_after_s > 0:
        seconds = float(retry_after_s)
        until_dt = ref_now_utc + timedelta(seconds=seconds)
    elif type_ == "sleeping":
        try:
            parts = day_start.strip().split(":")
            hh, mm = int(parts[0]), int(parts[1])
            now_wib = ref_now.astimezone(WIB)
            target_wib = now_wib.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if target_wib <= now_wib:
                target_wib += timedelta(days=1)
            until_dt = target_wib.astimezone(timezone.utc)
            seconds = max(0.0, (until_dt - ref_now_utc).total_seconds())
        except Exception as e:
            logger.warning("Failed parsing day_start %r: %s", day_start, e)
            seconds = 300.0
            until_dt = ref_now_utc + timedelta(seconds=seconds)
    else:
        seconds = float(DEFAULT_UNAVAILABLE_S.get(type_, 300))
        until_dt = ref_now_utc + timedelta(seconds=seconds)

    until_ts = until_dt.timestamp()
    ex_seconds = int(math.ceil(seconds)) + 60
    try:
        r = _redis()
        r.set(UNAVAILABLE_KEY, str(until_ts), ex=ex_seconds)
        r.set(UNAVAILABLE_REASON_KEY, type_, ex=ex_seconds)
    except Exception as e:
        logger.warning("Redis error in mark_gptcli_unavailable: %s", e)

    return until_dt


def generate_image(
    cfg: GptcliConfig,
    prompt: str,
    images: list[str],
    *,
    request_id: str,
) -> GptcliResult:
    url = f"{cfg.base_url.rstrip('/')}/v1/images/generations"
    payload = {
        "prompt": prompt,
        "images": images,
        "size": cfg.size,
        "thread": cfg.thread,
    }
    headers = {
        "Authorization": f"Bearer {cfg.token}",
        "Content-Type": "application/json",
        "X-Request-Id": request_id,
    }
    timeout = job_timeout_s(len(images)) + HTTP_MARGIN_S

    def _fail(
        type_: str,
        message: str,
        *,
        status: int | None = None,
        retry_after_s: int | None = None,
        extra: dict | None = None,
    ):
        if type_ in TEMPORARY_TYPES:
            mark_gptcli_unavailable(
                type_,
                retry_after_s,
                now=datetime.now(timezone.utc),
                day_start=cfg.day_start,
            )
        clean_msg = _sanitize(message, cfg.token)
        clean_extra = {}
        if extra:
            for k, v in extra.items():
                if isinstance(v, str):
                    clean_extra[k] = _sanitize(v, cfg.token)
                else:
                    clean_extra[k] = v
        raise GptcliError(
            type_,
            clean_msg,
            status=status,
            retry_after_s=retry_after_s,
            extra=clean_extra,
        )

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
    except (httpx.ConnectError, httpx.ConnectTimeout) as e:
        _fail("unreachable", f"Connect error: {e}")
    except httpx.TimeoutException as e:
        _fail("timeout", f"Timeout: {e}")
    except httpx.HTTPError as e:
        _fail("transport", f"Transport error: {e}")

    if resp.status_code == 200:
        try:
            body = resp.json()
            data = body.get("data")
            if not isinstance(data, list) or not data:
                raise ValueError("Response missing data list")
            b64 = data[0].get("b64_json")
            if not b64 or not isinstance(b64, str):
                raise ValueError("Response missing b64_json")
            png = base64.b64decode(b64)
            im = Image.open(io.BytesIO(png))
            im.verify()
        except Exception as e:
            _fail("bad_response", f"Invalid image response: {e}", status=200)

        meta = dict(body.get("gptcli") or {})
        return GptcliResult(png=png, meta=meta)

    # non-200
    try:
        body = resp.json()
        error_obj = body.get("error") if isinstance(body, dict) else {}
        error = error_obj if isinstance(error_obj, dict) else {}
    except Exception:
        error = {}

    type_ = error.get("type") or f"http_{resp.status_code}"
    message = error.get("message") or resp.text[:500]

    retry_after_s = None
    if "Retry-After" in resp.headers:
        try:
            retry_after_s = int(resp.headers["Retry-After"])
        except (ValueError, TypeError):
            retry_after_s = None

    extra = {k: v for k, v in error.items() if k not in ("type", "message")}
    _fail(
        type_,
        message,
        status=resp.status_code,
        retry_after_s=retry_after_s,
        extra=extra,
    )
