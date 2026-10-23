"""Account file loading and persisted state helpers for gemini-web-serve."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from datetime import datetime, timezone

log = logging.getLogger("gws.accounts")

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


def list_account_files(directory: str) -> dict[str, Path]:
    """Return {name: path} for valid account JSON files."""
    result: dict[str, Path] = {}
    d = Path(directory)
    if not d.is_dir():
        return result
    for f in sorted(d.iterdir()):
        if f.suffix != ".json" or not f.is_file():
            continue
        name = f.stem
        if _NAME_RE.match(name):
            result[name] = f
    return result


def load_account_file(path: Path) -> dict:
    """Load an account JSON file. Returns the parsed dict or empty on error."""
    try:
        with open(path, "r") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load account %s: %s", path, exc)
        return {}


def save_account_file(path: Path, data: dict) -> None:
    """Atomically write account data. Mode 0o600."""
    tmp = path.with_suffix(".json.tmp")
    content = json.dumps(data, indent=2, ensure_ascii=False)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    os.replace(str(tmp), str(path))


def account_status(acct_data: dict, default_proxy: str) -> tuple[str, str]:
    """Determine initial status and effective proxy for an account.

    Returns (status, proxy). status is one of: ok, needs_login, no_proxy.
    """
    cookies = acct_data.get("cookies", {})
    psid = cookies.get("__Secure-1PSID", "")
    if not psid:
        return "needs_login", ""

    proxy = acct_data.get("proxy", "") or default_proxy
    if not proxy:
        return "no_proxy", ""

    return "ok", proxy


def merge_cookies_from_jar(acct_data: dict, jar) -> bool:
    """Merge current client cookies into acct_data. Returns True if changed."""
    cookies = acct_data.setdefault("cookies", {})
    changed = False
    for c in jar:
        name = c.name if hasattr(c, "name") else str(c)
        value = c.value if hasattr(c, "value") else ""
        if value and cookies.get(name) != value:
            cookies[name] = value
            changed = True
    if changed:
        acct_data["updated_at"] = datetime.now(timezone.utc).isoformat()
    return changed


# --- Persisted state ---

def normalize_account_state(state: dict) -> dict:
    """Ensure all default keys exist in an account state dict."""
    defaults = default_account_state()
    defaults.update(state)
    return defaults


def load_state(path: str) -> dict[str, dict]:
    """Load per-account state from JSON file."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        with open(p, "r") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return {
                k: normalize_account_state(v)
                for k, v in data.items()
                if isinstance(v, dict)
            }
        return {}
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to load state %s: %s", path, exc)
        return {}


def save_state(path: str, state: dict) -> None:
    """Atomically write state JSON. Mode 0o600. Creates parent dir 0o700."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(str(p.parent), 0o700)
    except OSError:
        pass
    tmp = p.with_suffix(".json.tmp")
    content = json.dumps(state, indent=2, ensure_ascii=False)
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)
        os.write(fd, content.encode())
    finally:
        os.close(fd)
    os.replace(str(tmp), str(p))


def default_account_state() -> dict:
    """Return default state for a new account."""
    return {
        "status": "ok",
        "cooldown_until": None,
        "cooldown_level": 0,
        "last_used_at": None,
        "next_ok_at": None,
        "day": None,
        "used_today": 0,
        "cap_today": 0,
        "sleep_start": 0.0,
        "sleep_end": 0.0,
        "breaks": [],
        "last_error_type": None,
        "file_mtime": None,
    }
