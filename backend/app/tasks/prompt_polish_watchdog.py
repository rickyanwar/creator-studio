"""Watchdog task for Argon prompt polish health and owner escalation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import logging
import redis

from app.config import get_settings
from app.database import SessionLocal
from app.models.social_publishing import OperationalNotification
from app.services.prompt_polish_health import snapshot
from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)


def _redis():
    settings = get_settings()
    return redis.from_url(settings.redis_url, decode_responses=True)


def _to_utc_naive(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _is_owner_only(p: str) -> bool:
    return (
        p == "not_configured"
        or p.startswith("account_needs_login:")
        or p.startswith("account_needs_tos:")
        or p.startswith("account_abuse_flag:")
        or p.startswith("polish_failed:")
    )


def _send_notification(message: str, *, dedupe_key: str, db, notifier=None) -> None:
    if notifier is not None:
        notifier(message)
        return

    sent_ok = False
    try:
        from app.services.phone_runner_client import PhoneRunnerClient
        from app.services.social_delivery_dispatch import notify_owner_via_hermes

        client = PhoneRunnerClient()
        sent_ok, _ = notify_owner_via_hermes(
            message,
            dedupe_key=dedupe_key,
            db=db,
            runner_client=client,
        )
    except Exception as exc:
        logger.warning("notify_owner_via_hermes failed: %s", exc)
        sent_ok = False

    if not sent_ok:
        try:
            from app.tasks.phone_watchdog import send_telegram_fallback

            send_telegram_fallback(message, db)
        except Exception as exc:
            logger.warning("send_telegram_fallback failed: %s", exc)


@celery_app.task(name="app.tasks.prompt_polish_watchdog.check_prompt_polish", ignore_result=True)
def check_prompt_polish(db=None, now=None, notifier=None) -> None:
    """Check prompt polish health, maintain operational marker, and escalate."""
    opened_session = False
    if db is None:
        db = SessionLocal()
        opened_session = True

    try:
        now_dt = now or datetime.now(timezone.utc)
        now_naive = _to_utc_naive(now_dt)

        snap = snapshot(db, now=now_dt)
        problems = snap.get("problems", [])

        marker = (
            db.query(OperationalNotification)
            .filter_by(dedupe_key="gemini_web:serve")
            .first()
        )

        if not problems:
            if marker is not None and marker.state == "open":
                marker.state = "resolved"
                marker.message = "resolved"
                db.commit()

                alerted = False
                try:
                    r = _redis()
                    if r.get("prompt_polish:alerted"):
                        alerted = True
                        r.delete("prompt_polish:alerted")
                except Exception as exc:
                    logger.warning("Redis error checking prompt_polish:alerted: %s", exc)

                if alerted:
                    ts = int(now_naive.timestamp())
                    _send_notification(
                        "Argon prompt polish is healthy again.",
                        dedupe_key=f"gemini_web:resolved:{ts}",
                        db=db,
                        notifier=notifier,
                    )
            return

        opened_at = now_naive
        if marker is not None and marker.state == "open" and marker.last_error:
            try:
                data = json.loads(marker.last_error)
                parsed_open = datetime.fromisoformat(data["opened_at"])
                opened_at = _to_utc_naive(parsed_open) or now_naive
            except Exception:
                opened_at = now_naive

        msg_str = (", ".join(problems))[:500]
        last_err_str = json.dumps({"opened_at": opened_at.isoformat(), "problems": problems})

        if marker:
            marker.state = "open"
            marker.message = msg_str
            marker.last_error = last_err_str
        else:
            marker = OperationalNotification(
                dedupe_key="gemini_web:serve",
                kind="gemini_web_serve",
                state="open",
                message=msg_str,
                last_error=last_err_str,
            )
            db.add(marker)
        db.commit()

        owner_only = [p for p in problems if _is_owner_only(p)]
        healable = [p for p in problems if not _is_owner_only(p)]

        for p in owner_only:
            key = f"prompt_polish:alert:{p}"
            set_ok = False
            try:
                r = _redis()
                set_ok = bool(r.set(key, "1", nx=True, ex=86400))
            except Exception as exc:
                logger.warning("Redis unreachable when setting %s: %s; notifying anyway", key, exc)
                set_ok = True

            if set_ok:
                if p.startswith("account_needs_login:"):
                    name = p.split(":", 1)[1]
                    msg = f"Gemini account {name} is logged out. Log it in again: python3 /opt/gemini-web-serve/login.py {name}"
                elif p.startswith("account_needs_tos:"):
                    name = p.split(":", 1)[1]
                    msg = f"Gemini account {name} must accept the new Gemini Terms of Service. Run: python3 /opt/gemini-web-serve/login.py {name} and accept them at gemini.google.com."
                elif p.startswith("account_abuse_flag:"):
                    name = p.split(":", 1)[1]
                    msg = f"Gemini account {name} is flagged by Google and resting 24 h."
                elif p.startswith("polish_failed:"):
                    idea_id = p.split(":", 1)[1]
                    msg = f"Argon could not polish AI video idea {idea_id} after 3 rounds; the idea is marked failed."
                elif p == "not_configured":
                    msg = "Argon prompt polish is on but no gemini-web-serve token is set in Settings."
                else:
                    msg = f"Argon prompt polish issue: {p}"

                ts = int(now_naive.timestamp())
                _send_notification(
                    msg,
                    dedupe_key=f"gemini_web:{p}:{ts}",
                    db=db,
                    notifier=notifier,
                )
                try:
                    r = _redis()
                    r.set("prompt_polish:alerted", "1", ex=86400)
                except Exception:
                    pass

        if healable:
            if (now_naive - opened_at) >= timedelta(minutes=90):
                key = "prompt_polish:alerted"
                set_ok = False
                try:
                    r = _redis()
                    set_ok = bool(r.set(key, "1", nx=True, ex=21600))
                except Exception as exc:
                    logger.warning("Redis unreachable when setting %s: %s; notifying anyway", key, exc)
                    set_ok = True

                if set_ok:
                    probs_str = ", ".join(healable)
                    n_brands = snap.get("brands_on", 0)
                    msg = (
                        f"Argon prompt polish has been down for 90+ min: {probs_str}. "
                        f"Hermes could not fix it. AI videos of {n_brands} brands are waiting."
                    )
                    ts = int(now_naive.timestamp())
                    _send_notification(
                        msg,
                        dedupe_key=f"gemini_web:healable:{ts}",
                        db=db,
                        notifier=notifier,
                    )
    except Exception as exc:
        logger.exception("Error in check_prompt_polish: %s", exc)
    finally:
        if opened_session and db is not None:
            db.close()
