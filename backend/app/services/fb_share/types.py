from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class GroupInfo:
    id: int
    niche: str
    url: str
    name: str
    kind: str
    subject: str | None
    posting_identity: str
    allowed_days: tuple[int, ...] | None
    banned_content: tuple[str, ...] | None
    daily_cap: int = 1
    is_active: bool = True


@dataclass(frozen=True)
class ShareItem:
    id: int
    niche: str
    source_ref: str
    phash: str | None
    content_type: str | None
    subjects: tuple[str, ...] = ()
    sentiment: Mapping[str, str] = field(default_factory=dict)
    caption: str = ""
    hashtags: str = ""


@dataclass(frozen=True)
class SentRecord:
    group_id: int
    phash: str | None
    source_ref: str
    sent_at: datetime


@dataclass(frozen=True)
class PicMessage:
    kind: str
    text: str = ""
    media_path: str | None = None


@dataclass(frozen=True)
class ParsedReply:
    done: frozenset[int]
    not_member: frozenset[int]
    removed: frozenset[int]
    blocked: bool
    post_urls: Mapping[int, str]
    is_help: bool
    pending: frozenset[int] = frozenset()
