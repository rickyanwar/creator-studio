"""Print Gemini web serve (Argon prompt polish) health snapshot."""

from __future__ import annotations

import argparse
import json
import logging

from app.database import SessionLocal
from app.services.prompt_polish_health import snapshot

logger = logging.getLogger(__name__)


def _print_pretty(health: dict) -> None:
    print(f"Generated: {health.get('generated_at', '-')}")
    print(f"Configured: {health.get('configured')} | Brands on: {health.get('brands_on')} | OK: {health.get('ok')}")
    service = health.get("service") or {}
    accounts = service.get("accounts") or []
    print(f"{'account':<20} {'status':<15} {'available':<10} {'reason':<15} {'used/cap':<10}")
    for acct in accounts:
        name = acct.get("name", "-")
        st = acct.get("status", "-")
        avail = str(acct.get("available", False))
        reason = acct.get("reason") or "-"
        used = acct.get("used_today", 0)
        cap = acct.get("cap_today", 0)
        used_cap = f"{used}/{cap}"
        print(f"{name:<20} {st:<15} {avail:<10} {reason:<15} {used_cap:<10}")
    problems = health.get("problems", [])
    print(f"Problems: {', '.join(problems) if problems else 'none'}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pretty", action="store_true", help="print a human-readable table")
    args = parser.parse_args(argv)

    db = None
    try:
        db = SessionLocal()
        health = snapshot(db)
    except Exception as exc:
        logger.warning("Gemini web health unavailable: %s", exc)
        print(json.dumps({"error": "health_unavailable"}))
        return 2
    finally:
        if db is not None:
            db.close()

    if args.pretty:
        _print_pretty(health)
    else:
        print(json.dumps(health, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
