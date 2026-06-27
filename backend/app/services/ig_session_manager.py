"""instagrapi session manager.

Key rules (from spec §9):
- Load session from DB, call login() to reuse — never fresh login per task run.
- 1 sticky residential proxy per burner.
- Encrypt session JSON in DB via Fernet.
- Handle: ChallengeRequired, LoginRequired, PleaseWaitFewMinutes, 403/429.
"""

import json
import logging
import time
from datetime import datetime, timezone

from instagrapi import Client
from instagrapi.exceptions import (
    ChallengeRequired,
    ChallengeUnknownStep,
    LoginRequired,
    PleaseWaitFewMinutes,
    BadPassword,
    FeedbackRequired,
)

from app.services.encryption import encrypt, decrypt

logger = logging.getLogger(__name__)


class IGSessionManager:
    """Manages a single burner account's instagrapi Client."""

    def __init__(self, burner_record, db):
        self.burner = burner_record
        self.db = db
        self.client: Client | None = None

    # ─────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────

    def get_client(self) -> Client:
        """Return a ready, authenticated Client (reuses existing session)."""
        if self.client is not None:
            return self.client

        cl = Client()

        if self.burner.proxy_url:
            cl.set_proxy(self.burner.proxy_url)

        if self.burner.encrypted_session:
            try:
                session_json = decrypt(self.burner.encrypted_session)
                session_data = json.loads(session_json)
                cl.set_settings(session_data)
                logger.info("Loaded existing session for @%s", self.burner.ig_username)
            except Exception as exc:
                logger.warning("Could not load session for @%s: %s", self.burner.ig_username, exc)

        # login() reuses the loaded session; only does a fresh auth when session is absent/expired
        username = self.burner.ig_username
        password = decrypt(self.burner.encrypted_password)

        try:
            cl.login(username, password)
        except LoginRequired:
            logger.warning("Session expired for @%s, doing fresh login", username)
            cl = self._fresh_login(username, password)
        except (ChallengeRequired, ChallengeUnknownStep):
            self._mark_challenged()
            raise
        except PleaseWaitFewMinutes:
            self._mark_rate_limited()
            raise

        self._save_session(cl)
        self.client = cl
        return cl

    def fetch_recent_posts(self, ig_username: str, amount: int = 12) -> list:
        """Return recent media for an IG account using user_medias_v1."""
        cl = self.get_client()
        try:
            user_id = cl.user_id_from_username(ig_username)
            return cl.user_medias_v1(user_id, amount=amount)
        except FeedbackRequired:
            self._mark_feedback_required()
            raise
        except PleaseWaitFewMinutes:
            self._mark_rate_limited()
            raise
        except (ChallengeRequired, ChallengeUnknownStep):
            self._mark_challenged()
            raise

    # ─────────────────────────────────────────────
    # Internal helpers
    # ─────────────────────────────────────────────

    def _fresh_login(self, username: str, password: str) -> Client:
        cl = Client()
        if self.burner.proxy_url:
            cl.set_proxy(self.burner.proxy_url)
        try:
            cl.login(username, password)
        except (ChallengeRequired, ChallengeUnknownStep):
            self._mark_challenged()
            raise
        except BadPassword:
            self._mark_error("Bad password — update burner credentials")
            raise
        return cl

    def _save_session(self, cl: Client):
        """Encrypt and persist session JSON to DB."""
        try:
            session_json = json.dumps(cl.get_settings())
            self.burner.encrypted_session = encrypt(session_json)
            self.burner.last_used_at = datetime.now(timezone.utc)
            self.db.add(self.burner)
            self.db.commit()
        except Exception as exc:
            logger.error("Failed to save session for @%s: %s", self.burner.ig_username, exc)

    def _mark_challenged(self):
        from app.models.burner_accounts import BurnerStatus
        self.burner.status = BurnerStatus.challenged
        self.burner.last_error = "Challenged by Instagram (Bloks/OTP) — re-import session from browser"
        self.db.add(self.burner)
        self.db.commit()
        logger.error("Burner @%s is CHALLENGED — re-import session via ig_session_from_browser.py", self.burner.ig_username)

    def _mark_feedback_required(self):
        from app.models.burner_accounts import BurnerStatus
        from datetime import timedelta
        self.burner.status = BurnerStatus.rate_limited
        self.burner.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=2)
        self.burner.last_error = "FeedbackRequired — Instagram temporarily limited this account, cooling down 2h."
        self.db.add(self.burner)
        self.db.commit()
        logger.warning("Burner @%s FeedbackRequired — cooling down 2h", self.burner.ig_username)

    def _mark_rate_limited(self):
        from app.models.burner_accounts import BurnerStatus
        from datetime import timedelta
        self.burner.status = BurnerStatus.rate_limited
        self.burner.cooldown_until = datetime.now(timezone.utc) + timedelta(hours=24)
        self.burner.last_error = "PleaseWaitFewMinutes — cooling down 24h"
        self.db.add(self.burner)
        self.db.commit()
        logger.warning("Burner @%s rate-limited, cooldown 24h", self.burner.ig_username)

    def _mark_error(self, msg: str):
        self.burner.last_error = msg
        self.db.add(self.burner)
        self.db.commit()


# ── Burner auto-assignment ────────────────────────────────────────────────────

def get_least_used_burner(db):
    """Return the active burner with the fewest requests_today."""
    from app.models.burner_accounts import BurnerAccount, BurnerStatus
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    return (
        db.query(BurnerAccount)
        .filter(
            BurnerAccount.status == BurnerStatus.active,
            (BurnerAccount.cooldown_until == None) | (BurnerAccount.cooldown_until <= now),
        )
        .order_by(BurnerAccount.requests_today.asc())
        .first()
    )
