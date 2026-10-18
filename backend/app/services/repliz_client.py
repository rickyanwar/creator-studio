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


def format_schedule_at(dt: datetime) -> str:
    """Render a datetime (naive = UTC, or tz-aware) as the ISO 8601 UTC
    string Repliz's scheduleAt expects."""
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _schedule_at_now_plus(seconds: int = 60) -> str:
    """Return ISO 8601 UTC timestamp 'now + seconds'."""
    return format_schedule_at(datetime.now(timezone.utc) + timedelta(seconds=seconds))


REPLIZ_DOCUMENTED_ACCOUNT_TYPES = (
    "facebook",
    "instagram",
    "threads",
    "youtube",
)
REPLIZ_SUPPORTED_ACCOUNT_TYPES = (
    "facebook",
    "instagram",
    "threads",
    "youtube",
    "x",
)


class ReplizPlanRequired(Exception):
    pass


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
    # Account listing
    # ─────────────────────────────────────────────

    def _fetch_accounts_raw(self, params: dict[str, Any]) -> list[dict]:
        resp = self._client.get("/public/account", params=params)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            items = data.get("docs", data.get("data", []))
        elif isinstance(data, list):
            items = data
        else:
            items = []
        return [item for item in items if isinstance(item, dict)]

    def list_accounts(
        self,
        types: list[str] | tuple[str, ...] | None = None,
        page: int = 1,
        limit: int = 50,
    ) -> list[dict]:
        """Return connected accounts from Repliz for given platform types.

        Fetches with documented types (facebook, instagram, threads, youtube).
        If fetching all or if twitter/x is requested, adds one extra call without
        any types filter, client-side filtered to type in ('twitter', 'x'),
        merging and de-duplicating by id. Maps 'twitter'/'x' -> platform 'x'.
        """
        include_x = False
        if types is None:
            documented = list(REPLIZ_DOCUMENTED_ACCOUNT_TYPES)
            include_x = True
        else:
            documented = [t for t in types if t in REPLIZ_DOCUMENTED_ACCOUNT_TYPES]
            include_x = any(t in ("twitter", "x") for t in types)

        raw_accounts: list[dict] = []

        if documented:
            params: dict[str, Any] = {"page": page, "limit": limit}
            for i, t in enumerate(documented):
                params[f"types[{i}]"] = t
            raw_accounts.extend(self._fetch_accounts_raw(params))

        if include_x:
            extra_params: dict[str, Any] = {"page": page, "limit": limit}
            extra_docs = self._fetch_accounts_raw(extra_params)
            for doc in extra_docs:
                raw_type = str(doc.get("type") or doc.get("platform") or "").lower()
                if raw_type in ("twitter", "x"):
                    raw_accounts.append(doc)

        seen_ids: set[str] = set()
        merged: list[dict] = []
        for doc in raw_accounts:
            acc_id = str(doc.get("_id") or doc.get("id") or "").strip()
            dedup_key = acc_id or str(id(doc))
            if dedup_key in seen_ids:
                continue
            seen_ids.add(dedup_key)

            raw_type = str(doc.get("type") or doc.get("platform") or "").lower()
            if raw_type in ("twitter", "x"):
                doc["platform"] = "x"
            elif "platform" not in doc and raw_type:
                doc["platform"] = raw_type
            merged.append(doc)

        return merged

    def list_facebook_accounts(self, page: int = 1, limit: int = 50) -> list[dict]:
        """Return all connected Facebook accounts from Repliz."""
        return self.list_accounts(types=["facebook"], page=page, limit=limit)

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

    def create_video_schedule(
        self,
        account_id: str,
        description: str,
        video_url: str,
        thumbnail_url: str = "",
        title: str = "",
        schedule_at: str | None = None,
    ) -> dict:
        """Schedule a video (Reel) post — Mode 7 clips. Same payload shape
        jipraks/yt-short-clipper's repliz.py posts video with. Not flagged as
        AI-generated: it's real footage, only cut and reframed."""
        payload = {
            "title": title,
            "description": description,
            "type": "video",
            "medias": [{"type": "video", "thumbnail": thumbnail_url, "url": video_url}],
            "accountId": account_id,
            "scheduleAt": schedule_at or _schedule_at_now_plus(60),
            "additionalInfo": {"isAiGenerated": False, "isDraft": False},
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

    def get_content_statistic(self, content_id: str, account_id: str) -> dict:
        resp = self._client.get(
            f"/public/content/{content_id}/statistic",
            params={"accountId": account_id},
        )
        if resp.status_code == 402:
            raise ReplizPlanRequired(resp.text[:200])
        resp.raise_for_status()
        return resp.json()

    def get_content(
        self,
        account_id: str,
        content_type: str = "media",
        next_token: str | None = None,
    ) -> dict:
        """Fetch published content for a connected Facebook account. Gold+ plan required.

        Contract: GET /public/content, Basic auth, accountId required, type (media/story), nextToken.
        https://docs.repliz.com/api/content/get-content
        """
        if not account_id or not str(account_id).strip():
            raise ValueError("accountId is required")
        params: dict[str, Any] = {
            "accountId": str(account_id).strip(),
            "type": content_type,
        }
        if next_token:
            params["nextToken"] = next_token
        resp = self._client.get("/public/content", params=params)
        if resp.status_code == 402:
            raise ReplizPlanRequired(resp.text[:200])
        resp.raise_for_status()
        return resp.json()

    # ─────────────────────────────────────────────
    # Deletion — removes the schedule/post on Repliz (and, per Repliz, on the
    # connected Facebook page). Not verified against live Repliz docs; follows
    # the same REST convention as get_schedule() (same resource path).
    # ─────────────────────────────────────────────

    def delete_schedule(self, schedule_id: str) -> dict:
        """Delete a scheduled/published post on Repliz (and its live Facebook
        post). Raises httpx.HTTPStatusError on failure — caller decides how to
        handle a Repliz-side failure vs. the local record."""
        resp = self._client.delete(f"/public/schedule/{schedule_id}")
        resp.raise_for_status()
        try:
            return resp.json()
        except ValueError:
            return {}


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
