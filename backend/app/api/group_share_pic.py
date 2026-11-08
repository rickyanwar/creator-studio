"""Scoped inbound replies from registered Group Share PIC contacts."""

import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import DB, require_hermes_scope
from app.models.fb_group_share import FbGroup, FbGroupMembership, FbShareDelivery, FbShareTask
from app.models.social_publishing import SocialDestination
from app.services.fb_share.pic_messages import OFF_TOPIC_TEXT, confirmation_text
from app.services.fb_share.pic_replies import parse_reply
from app.services.phone_runner_client import PhoneRunnerClient

router = APIRouter(prefix="/group-share", tags=["group-share"])


class PicReplyIn(BaseModel):
    channel: Literal["whatsapp", "telegram"]
    sender: str
    username: str | None = Field(default=None, max_length=64)
    text: str = Field(max_length=2000)


def normalize_sender(sender: str, channel: str) -> str:
    if channel == "whatsapp":
        return re.sub(r"[^0-9]", "", sender.split("@", 1)[0].split(":", 1)[0])
    return sender.lstrip("@").casefold()


@router.post("/pic-reply")
def pic_reply(body: PicReplyIn, db: DB, token=Depends(require_hermes_scope("group_share:pic"))):
    senders = {normalize_sender(body.sender, body.channel)}
    if body.channel == "telegram" and body.username:
        senders.add(normalize_sender(body.username, body.channel))
    senders.discard("")
    deliveries = [delivery for delivery in db.query(FbShareDelivery).filter(
        FbShareDelivery.mode == f"pic_{body.channel}",
    ).all() if normalize_sender(delivery.pic_contact or "", body.channel) in senders]
    if not deliveries:
        return {"handled": False, "reply": None}

    bundles = {}
    for task in db.query(FbShareTask).filter(
        FbShareTask.destination_id.in_([d.destination_id for d in deliveries]),
        FbShareTask.pic_message_ref.is_not(None),
    ).order_by(FbShareTask.id).with_for_update().all():
        bundles.setdefault((task.destination_id, task.pic_message_ref), []).append(task)
    open_bundles = [tasks for tasks in bundles.values() if any(t.state == "sent_to_pic" for t in tasks)]
    bundle = max(open_bundles, key=lambda tasks: max(t.updated_at for t in tasks), default=[])
    urls = [db.get(FbGroup, task.group_id).url for task in bundle]
    parsed = parse_reply(body.text, urls)
    if not bundle and not parsed.blocked:
        db.commit()
        return {"handled": True, "reply": OFF_TOPIC_TEXT if parsed.is_help else "No open task right now."}

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    delivery = next(d for d in deliveries if d.destination_id == bundle[0].destination_id) if bundle else deliveries[0]
    done, not_member, removed = set(), set(), set()
    if parsed.blocked:
        for tasks in bundles.values():
            for task in tasks:
                if task.destination_id == delivery.destination_id and task.state == "sent_to_pic":
                    task.state, task.updated_at = "blocked", now
        delivery.paused_until = now + timedelta(hours=72)
        delivery.block_count = (delivery.block_count or 0) + 1
    else:
        for number, task in enumerate(bundle, 1):
            if task.state != "sent_to_pic":
                continue
            if number in parsed.done:
                task.state, task.updated_at = "published", now
                if number in parsed.post_urls:
                    task.post_url = parsed.post_urls[number]
                done.add(number)
            elif number in parsed.not_member or number in parsed.removed:
                status = "removed" if number in parsed.removed else "not_member"
                task.state, task.updated_at = "not_member", now
                (removed if status == "removed" else not_member).add(number)
                membership = db.query(FbGroupMembership).filter_by(
                    destination_id=task.destination_id, group_id=task.group_id,
                ).first()
                if membership is None:
                    membership = FbGroupMembership(destination_id=task.destination_id, group_id=task.group_id)
                    db.add(membership)
                membership.status, membership.checked_at = status, now
    remaining = sum(task.state == "sent_to_pic" for task in bundle)
    reply = OFF_TOPIC_TEXT if parsed.is_help and not parsed.blocked else confirmation_text(done, not_member, removed, parsed.blocked, remaining)
    db.commit()
    if parsed.blocked:
        try:
            destination = db.get(SocialDestination, delivery.destination_id)
            PhoneRunnerClient().notify_owner(f"Group Share: {destination.name} reported blocked by PIC; paused 72 h")
        except Exception:
            pass
    return {"handled": True, "reply": reply}
