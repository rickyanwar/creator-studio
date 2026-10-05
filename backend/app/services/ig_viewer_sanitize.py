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


def describe_failure(kind: str | None, error: str | None) -> str:
    error_lower = str(error).lower() if error else ""
    if kind == "script":
        if "posts clicked=false" in error_lower:
            return "Posts tab did not appear on the viewer page"
        if "no posts captured" in error_lower:
            return "Page loaded but no posts were returned (account may be private, renamed, deleted or have no posts)"
        if "not found or private" in error_lower:
            return "Viewer says the account was not found or is private"
        if "parse failed" in error_lower:
            return "Viewer returned data in an unexpected format (our parser may need an update)"
        return "The viewer page changed format or did not load completely"
    if kind == "blocked":
        return "Cloudflare check was not passed"
    if kind == "site_down":
        if "timeout" in error_lower:
            return "The viewer site did not respond in time (site slow or server busy)"
        return "The viewer site is unreachable (HTTP 5xx / network)"
    return "An unknown error occurred"


def describe_success(posts: int | None) -> str:
    if posts is None:
        return "OK"
    return f"OK — {posts} posts"
