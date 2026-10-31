import hmac
import os
from pathlib import Path
import re

SERIAL_REGEX = re.compile(r"^[a-zA-Z0-9.:_-]{1,64}$")


def get_local_app_card_version(card_path: Path | None = None) -> str | None:
    target_path = card_path if card_path is not None else (Path(__file__).parent / "app_cards" / "android" / "facebook.md")
    if target_path.is_file():
        try:
            content = target_path.read_text(encoding="utf-8")
            m = re.search(r"(?:\*{1,2})?Version(?:\*{1,2})?:?(?:\*{1,2})?[:\s]+(\d+\.\d+\.\d+)", content, re.IGNORECASE)
            if m:
                return m.group(1)
        except OSError:
            pass
    return None


def get_bind_host() -> str:
    return os.environ.get("PHONE_RUNNER_BIND", "127.0.0.1")


def get_bind_port() -> int:
    try:
        return int(os.environ.get("PHONE_RUNNER_PORT", "8765"))
    except ValueError:
        return 8765


def get_runner_token() -> str:
    return os.environ.get("PHONE_RUNNER_TOKEN", "").strip()


def get_allowed_serials() -> set[str]:
    raw = os.environ.get("PHONE_RUNNER_ALLOWED_SERIALS", "")
    return {s.strip() for s in raw.split(",") if s.strip()}


def get_adb_path() -> str:
    return os.environ.get("ADB_PATH", "adb")


def get_mobilerun_path() -> str:
    return os.environ.get("MOBILERUN_PATH", "/root/.local/bin/mobilerun")


def get_mobilerun_gateway_url() -> str:
    return (
        os.environ.get("MOBILERUN_API_BASE", "").strip()
        or os.environ.get("MOBILERUN_GATEWAY_URL", "http://localhost:20128/v1").strip()
    )


def get_mobilerun_model() -> str:
    return os.environ.get("MOBILERUN_MODEL", "FlashFast")


def get_mobilerun_provider() -> str:
    return os.environ.get("MOBILERUN_PROVIDER", "OpenAILike").strip() or "OpenAILike"


def get_default_timeout() -> float:
    try:
        return float(os.environ.get("DEFAULT_TIMEOUT_SEC", "15.0"))
    except ValueError:
        return 15.0


def get_mobilerun_timeout() -> float:
    try:
        return float(os.environ.get("MOBILERUN_TIMEOUT_SEC", "60.0"))
    except ValueError:
        return 60.0


def is_serial_valid_format(serial: str) -> bool:
    if not isinstance(serial, str):
        return False
    return bool(SERIAL_REGEX.match(serial))


def is_serial_allowed(serial: str) -> bool:
    if not is_serial_valid_format(serial):
        return False
    allowed = get_allowed_serials()
    return serial in allowed


def get_allowed_media_hosts() -> set[str]:
    raw = os.environ.get("PHONE_RUNNER_MEDIA_HOSTS", "")
    return {s.strip().lower() for s in raw.split(",") if s.strip()}


def get_max_media_bytes() -> int:
    try:
        return int(os.environ.get("PHONE_RUNNER_MAX_MEDIA_BYTES", str(300 * 1024 * 1024)))
    except ValueError:
        return 300 * 1024 * 1024


def get_media_timeout() -> float:
    try:
        return float(os.environ.get("PHONE_RUNNER_MEDIA_TIMEOUT_SEC", "30.0"))
    except ValueError:
        return 30.0


def is_media_host_allowed(host: str) -> bool:
    if not host or not isinstance(host, str):
        return False
    norm_host = host.strip().lower().split(":")[0]
    allowed = get_allowed_media_hosts()
    if not allowed:
        return False
    for pat in allowed:
        if pat == norm_host:
            return True
        if pat.startswith("*.") and norm_host.endswith(pat[1:]):
            return True
        if pat.startswith(".") and norm_host.endswith(pat):
            return True
    return False


def verify_token(provided_token: str | None) -> bool:
    if not provided_token:
        return False
    expected = get_runner_token()
    if not expected:
        return False
    return hmac.compare_digest(provided_token.strip().encode("utf-8"), expected.encode("utf-8"))


def sanitize_actor_name_for_prompt(name: str | None) -> str:
    """Sanitize actor/destination name for safe interpolation into agent prompts.

    Preserves Unicode letters and numbers across languages.
    Removes quotes, backticks, newlines, control characters, and injection delimiters.
    Collapses whitespace and bounds length.
    """
    if not name:
        return "Unknown"
    cleaned = re.sub(r"[\r\n\t\x00-\x1f\x7f-\x9f]", " ", str(name))
    cleaned = re.sub(r"['\"`<>\[\]{}\\]", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > 100:
        cleaned = cleaned[:100].strip()
    return cleaned or "Unknown"
