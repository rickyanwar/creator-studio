"""Repliz API client — Premium+ required.

Base URL : https://api.repliz.com
Auth     : Basic Base64(AccessKey:SecretKey)
"""

import base64
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)

settings = get_settings()


def _build_auth_header(access_key: str, secret_key: str) -> str:
    token = base64.b64encode(f"{access_key}:{secret_key}".encode()).decode()
    return f"Basic {token}"


def _schedule_at_now_plus(seconds: int = 60) -> str:
    """Return ISO 8601 UTC timestamp 'now + seconds'."""
    dt = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


class ReplizClient:
    def __init__(self, access_key: str, secret_key: str, base_url: str | None = None):
        self.base_url = (base_url or settings.repliz_base_url).rstrip("/")
        self._auth = _build_auth_header(access_key, secret_key)
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "Authorization": self._auth,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=30.0,
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # ─────────────────────────────────────────────
    # Facebook account listing
    # ─────────────────────────────────────────────

    def list_facebook_accounts(self, page: int = 1, limit: int = 50) -> list[dict]:
        """Return all connected Facebook accounts from Repliz."""
        resp = self._client.get(
            "/public/account",
            params={"page": page, "limit": limit, "types[0]": "facebook"},
        )
        resp.raise_for_status()
        data = resp.json()
        # Repliz returns { docs: [...], totalDocs: N, ... }
        if isinstance(data, dict):
            return data.get("docs", data.get("data", []))
        return data

    # ─────────────────────────────────────────────
    # Schedule creation
    # ─────────────────────────────────────────────

    def create_image_schedule(
        self,
        account_id: str,
        description: str,
        image_url: str,
        alt: str = "",
        schedule_at: str | None = None,
    ) -> dict:
        """Schedule a single-image post to a Facebook fanpage."""
        payload = {
            "title": "",
            "description": description,
            "type": "image",
            "medias": [
                {
                    "alt": alt,
                    "type": "image",
                    "thumbnail": image_url,
                    "url": image_url,
                }
            ],
            "accountId": account_id,
            "scheduleAt": schedule_at or _schedule_at_now_plus(60),
            "additionalInfo": {"isAiGenerated": True, "isDraft": False},
        }
        resp = self._client.post("/public/schedule", json=payload)
        resp.raise_for_status()
        return resp.json()

    def create_album_schedule(
        self,
        account_id: str,
        description: str,
        image_urls: list[str],
        schedule_at: str | None = None,
    ) -> dict:
        """Schedule a multi-image (album) post to a Facebook fanpage."""
        medias = [
            {"alt": "", "type": "image", "thumbnail": url, "url": url}
            for url in image_urls
        ]
        payload = {
            "title": "",
            "description": description,
            "type": "album",
            "medias": medias,
            "accountId": account_id,
            "scheduleAt": schedule_at or _schedule_at_now_plus(60),
            "additionalInfo": {"isAiGenerated": True, "isDraft": False},
        }
        resp = self._client.post("/public/schedule", json=payload)
        resp.raise_for_status()
        return resp.json()

    # ─────────────────────────────────────────────
    # Status polling
    # ─────────────────────────────────────────────

    def get_schedule(self, schedule_id: str) -> dict:
        """Poll status of a scheduled post."""
        resp = self._client.get(f"/public/schedule/{schedule_id}")
        resp.raise_for_status()
        return resp.json()


# ── Factory helper ────────────────────────────────────────────────────────────

def get_repliz_client(access_key: str, secret_key: str) -> ReplizClient:
    return ReplizClient(access_key=access_key, secret_key=secret_key)


def get_repliz_client_from_env() -> ReplizClient:
    """Build a client from environment variables (for CLI/testing)."""
    s = get_settings()
    if not s.repliz_access_key or not s.repliz_secret_key:
        raise RuntimeError("REPLIZ_ACCESS_KEY and REPLIZ_SECRET_KEY must be set")
    return ReplizClient(access_key=s.repliz_access_key, secret_key=s.repliz_secret_key)


def get_repliz_client_from_db(db) -> ReplizClient:
    """Build a client from encrypted credentials stored in the database."""
    from app.models.settings import Settings as DBSettings
    from app.services.encryption import decrypt

    row = db.query(DBSettings).filter_by(id=1).first()
    if not row or not row.repliz_access_key_encrypted:
        raise RuntimeError("Repliz credentials not configured in Settings")
    return ReplizClient(
        access_key=decrypt(row.repliz_access_key_encrypted),
        secret_key=decrypt(row.repliz_secret_key_encrypted),
    )
