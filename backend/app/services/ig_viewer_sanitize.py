"""Remove secrets and terminal controls from viewer diagnostics."""

import re
from urllib.parse import urlsplit, urlunsplit


_URL = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_TOKEN = re.compile(r"\b(?:github_pat_|gh[a-z]_|sk-)[A-Za-z0-9_\-]+\b", re.IGNORECASE)
_BEARER = re.compile(r"\bBearer\s+\S+", re.IGNORECASE)
_CONTROLS = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_error(text: str, limit: int = 300) -> str:
    """Keep URL scheme/host/path; remove credentials, query, fragment and tokens."""
    text = _CONTROLS.sub("", str(text))

    def clean_url(match):
        url = match.group()
        parts = urlsplit(url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        try:
            if parts.port:
                host += f":{parts.port}"
        except ValueError:
            pass
        return urlunsplit((parts.scheme, host, parts.path, "", ""))

    text = _URL.sub(clean_url, text)
    text = _BEARER.sub("Bearer [REDACTED]", text)
    return _TOKEN.sub("[REDACTED]", text)[:limit]
