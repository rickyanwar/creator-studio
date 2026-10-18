#!/usr/bin/env python3
"""Direct Codex usage reader from OpenAI Wham usage API.
Reads active Codex credentials from 9Router SQLite DB and queries OpenAI directly.
Outputs ONE JSON line to stdout or exits 1 on failure. Never prints or logs tokens."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import sqlite3
import sys
import urllib.error
import urllib.request

DEFAULT_DB_URI = "file:/root/.9router/db/data.sqlite?mode=ro"
USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
TIMEOUT_S = 20


def get_connections(db_uri: str) -> list[tuple[str, str, str]]:
    try:
        conn = sqlite3.connect(db_uri, uri=True)
        cur = conn.cursor()
        cur.execute(
            "SELECT id, data FROM providerConnections WHERE provider='codex' AND isActive=1"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as exc:
        sys.stderr.write(f"db query error: {type(exc).__name__}: {exc}\n")
        return []

    results = []
    for row_id, data_str in rows:
        try:
            data = json.loads(data_str)
            token = data.get("accessToken")
            account_id = data.get("providerSpecificData", {}).get("chatgptAccountId")
            if token:
                results.append((str(row_id), str(token), str(account_id or "")))
        except Exception:
            continue
    return results


def parse_usage(body_str: str) -> dict | None:
    try:
        payload = json.loads(body_str)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    rl = payload.get("rate_limit")
    if not isinstance(rl, dict):
        return None

    pw = rl.get("primary_window")
    if not isinstance(pw, dict):
        return None

    limit_reached = bool(rl.get("limit_reached"))
    if limit_reached:
        window_pct = 100.0
    else:
        raw_used = pw.get("used_percent")
        if raw_used is None or isinstance(raw_used, bool):
            return None
        try:
            window_pct = float(raw_used)
        except (ValueError, TypeError):
            return None

    weekly_pct = None
    sw = rl.get("secondary_window")
    if isinstance(sw, dict):
        raw_sw = sw.get("used_percent")
        if raw_sw is not None and not isinstance(raw_sw, bool):
            try:
                weekly_pct = float(raw_sw)
            except (ValueError, TypeError):
                weekly_pct = None

    resets_at = None
    if "reset_at" in pw and pw["reset_at"] is not None and not isinstance(pw["reset_at"], bool):
        try:
            epoch = float(pw["reset_at"])
            dt = datetime.fromtimestamp(epoch, tz=timezone.utc).replace(microsecond=0)
            resets_at = dt.isoformat()
        except Exception:
            pass
    elif "reset_after_seconds" in pw and pw["reset_after_seconds"] is not None and not isinstance(pw["reset_after_seconds"], bool):
        try:
            secs = float(pw["reset_after_seconds"])
            dt = (datetime.now(timezone.utc) + timedelta(seconds=secs)).replace(microsecond=0)
            resets_at = dt.isoformat()
        except Exception:
            pass

    return {
        "window_pct": window_pct,
        "weekly_pct": weekly_pct,
        "resets_at": resets_at,
    }


def fetch_usage(token: str, account_id: str) -> str:
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "ChatGPT-Account-Id": account_id,
        "User-Agent": "codex-cli",
    }
    req = urllib.request.Request(USAGE_URL, headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=TIMEOUT_S) as resp:
        return resp.read().decode("utf-8")


def main() -> None:
    db_uri = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB_URI
    connections = get_connections(db_uri)
    if not connections:
        sys.stderr.write("no active codex connections in db\n")
        sys.exit(1)

    shapes = []
    errors = []
    for conn_id, token, account_id in connections:
        try:
            body = fetch_usage(token, account_id)
            parsed = parse_usage(body)
            if parsed is None:
                errors.append(f"conn {conn_id}: invalid usage payload")
                continue
            shapes.append(parsed)
        except urllib.error.HTTPError as err:
            errors.append(f"conn {conn_id}: HTTP {err.code}")
        except urllib.error.URLError as err:
            errors.append(f"conn {conn_id}: URL error: {err.reason}")
        except Exception as exc:
            errors.append(f"conn {conn_id}: {type(exc).__name__}")

    if not shapes:
        sys.stderr.write(f"usage fetch failed: {'; '.join(errors)}\n")
        sys.exit(1)

    shapes.sort(key=lambda s: s["window_pct"])
    best = shapes[0]

    out = {
        "window_pct": best["window_pct"],
        "weekly_pct": best["weekly_pct"],
        "resets_at": best["resets_at"],
        "source": "openai-direct",
        "accounts": len(shapes),
    }
    print(json.dumps(out))
    sys.exit(0)


if __name__ == "__main__":
    main()
