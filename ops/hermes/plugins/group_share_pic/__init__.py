"""Intercept PIC direct messages before Hermes authorization or AI dispatch."""

import asyncio
import json
import logging
import os
import time
from urllib.request import Request, urlopen


logger = logging.getLogger(__name__)
_not_handled = {}
_warned_missing_token = False


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", on_message)


def _owner_id(platform, value):
    value = str(value).strip()
    if platform == "whatsapp":
        value = value.split("@", 1)[0].split(":", 1)[0]
        return "".join(char for char in value if char.isdigit())
    return value


def _post_reply(base, token, payload):
    request = Request(
        base.rstrip("/") + "/group-share/pic-reply",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=10) as response:
        if response.status != 200:
            raise ValueError("Unexpected HTTP status")
        data = json.loads(response.read())
    if not isinstance(data, dict) or type(data.get("handled")) is not bool:
        raise ValueError("Invalid PIC response")
    return data


async def on_message(event, gateway, session_store, **kwargs):
    global _warned_missing_token

    source = event.source
    platform = getattr(source.platform, "value", source.platform)
    if platform not in ("whatsapp", "telegram") or source.chat_type not in ("dm", "private"):
        return None

    sender = getattr(source, "user_id", None)
    allowed = os.environ.get(f"{platform.upper()}_ALLOWED_USERS", "").split(",")
    if sender and _owner_id(platform, sender) in {
        _owner_id(platform, user) for user in allowed if user.strip()
    }:
        return None

    token = os.environ.get("STUDIO_PIC_TOKEN", "").strip()
    if not token:
        if not _warned_missing_token:
            logger.error("group-share-pic misconfigured: STUDIO_PIC_TOKEN is missing")
            _warned_missing_token = True
        return {"action": "skip", "reason": "group-share-pic-misconfigured"}

    try:
        if not sender:
            raise ValueError("Missing sender")
        sender = str(sender)
        now = time.monotonic()
        for key, expires in list(_not_handled.items()):
            if expires <= now:
                del _not_handled[key]
        key = (platform, sender)
        if key in _not_handled:
            return None

        payload = {"channel": platform, "sender": sender, "text": event.text or ""}
        username = getattr(source, "username", None) or getattr(source, "user_name", None)
        if username:
            payload["username"] = username
        data = await asyncio.to_thread(
            _post_reply,
            os.environ.get("STUDIO_API_BASE", "https://autostudio.pro/api"),
            token,
            payload,
        )
        if data["handled"] is False:
            _not_handled[key] = time.monotonic() + 600
            return None
        reply = data.get("reply")
        if isinstance(reply, str) and reply:
            result = await gateway.adapters[source.platform].send(
                chat_id=source.chat_id, content=reply,
            )
            if getattr(result, "success", True) is False:
                raise RuntimeError("Reply delivery failed")
        return {"action": "skip", "reason": "group-share-pic"}
    except Exception as exc:
        # Exception messages and tracebacks can contain credentials or PIC text.
        logger.error("group-share-pic routing failed (%s)", type(exc).__name__)
        return {"action": "skip", "reason": "group-share-pic-error"}
