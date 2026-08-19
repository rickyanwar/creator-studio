"""Effective 9Router config resolver.

9Router (base URL / API key / model) can be configured two ways:
  1. Web UI → DB Settings (nine_router_* columns) — takes priority.
  2. NINE_ROUTER_* env vars (config.py) — fallback / defaults.

`get_nine_router_config()` merges them (DB overrides env, field by field) and
caches the result briefly so callers don't hit the DB on every request. Edits
in the settings UI take effect within the TTL.
"""

import logging
import time
from dataclasses import dataclass

from app.config import get_settings as _env_settings

logger = logging.getLogger(__name__)

_TTL = 30.0
_cache: dict = {"cfg": None, "at": 0.0}

# Built-in default for discussion_model when Settings.nine_router_discussion_model
# is unset — a stronger combo route specifically for Mode 4 hot-take/discussion
# copy (2026-08-20, user request), kept separate from the general `model` (used
# by news copy and everything else) so it can be dialed back from the Settings
# UI without touching the rest of the pipeline if it misbehaves.
_DEFAULT_DISCUSSION_MODEL = "smart-combo"


@dataclass
class NineRouterConfig:
    base_url: str
    api_key: str
    model: str
    discussion_model: str

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


def _resolve() -> NineRouterConfig:
    env = _env_settings()
    base_url = env.nine_router_base_url
    api_key = env.nine_router_api_key
    model = env.nine_router_model
    discussion_model = _DEFAULT_DISCUSSION_MODEL

    try:
        from app.database import SessionLocal
        from app.models.settings import Settings
        from app.services.encryption import decrypt

        db = SessionLocal()
        try:
            row = db.query(Settings).filter_by(id=1).first()
            if row:
                if row.nine_router_base_url:
                    base_url = row.nine_router_base_url
                if row.nine_router_model:
                    model = row.nine_router_model
                if row.nine_router_api_key_encrypted:
                    api_key = decrypt(row.nine_router_api_key_encrypted)
                if row.nine_router_discussion_model:
                    discussion_model = row.nine_router_discussion_model
        finally:
            db.close()
    except Exception as exc:  # never let a settings read break AI calls
        logger.warning("9Router config DB read failed, using env defaults: %s", exc)

    return NineRouterConfig(
        base_url=(base_url or "").rstrip("/"), api_key=api_key or "", model=model or "",
        discussion_model=discussion_model,
    )


def get_nine_router_config(force: bool = False) -> NineRouterConfig:
    now = time.time()
    if not force and _cache["cfg"] and now - _cache["at"] < _TTL:
        return _cache["cfg"]
    cfg = _resolve()
    _cache["cfg"] = cfg
    _cache["at"] = now
    return cfg


def clear_cache() -> None:
    """Force the next read to re-resolve (called after a settings save)."""
    _cache["cfg"] = None
    _cache["at"] = 0.0
