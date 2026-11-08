import unicodedata
from collections.abc import Collection, Sequence

from app.services.radar_clustering import phash_distance

from .types import GroupInfo, SentRecord, ShareItem


def normalize_subject(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s.lower())
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).split())


def select_groups(
    item: ShareItem,
    groups: Sequence[GroupInfo],
    joined_group_ids: Collection[int],
    identity_kind: str,
    sent_history: Sequence[SentRecord],
    weekday: int,
    max_groups: int,
    phash_max_distance: int = 10,
) -> list[GroupInfo]:
    if max_groups <= 0:
        return []
    sentiment = {normalize_subject(key): value for key, value in item.sentiment.items()}
    selected = []
    for group in groups:
        if not group.is_active or group.niche.lower() != item.niche.lower() or group.id not in joined_group_ids:
            continue
        if identity_kind == "page" and group.posting_identity == "profile_only":
            continue
        if group.allowed_days is not None and weekday not in group.allowed_days:
            continue
        if group.banned_content is not None and item.content_type in group.banned_content:
            continue
        if group.kind in ("person_fan", "team_fan"):
            subject = normalize_subject(group.subject or "")
            if not subject or sentiment.get(subject) != "positive":
                continue
        if group.kind == "meme" and item.content_type != "meme":
            continue
        if any(
            record.group_id == group.id and (
                record.source_ref == item.source_ref or (
                    record.phash and item.phash
                    and phash_distance(record.phash, item.phash) <= phash_max_distance
                )
            )
            for record in sent_history
        ):
            continue
        selected.append(group)
    priority = {"person_fan": 0, "team_fan": 0, "meme": 1, "general": 2}
    return sorted(selected, key=lambda group: (priority[group.kind], group.name.lower()))[:max_groups]
