from collections.abc import Collection, Sequence

from .types import PicMessage


ONBOARDING_TEXT = (
    'Studio group tasks.\nEach task: page name, photo or video, caption, then the group links.\n'
    'Post the photo with the caption in each group, about 30 minutes apart.\nReply with the number:\n'
    '"1 sudah" = posted\n"2 bukan anggota" = not a member\n"3 dikeluarkan" = removed from the group\n'
    '"diblokir" = Facebook blocked posting\nYou can also paste the post link.'
)
OFF_TOPIC_TEXT = 'This number only records Group Share tasks. Reply like "1 sudah" or "2 bukan anggota".'


def build_task_messages(
    page_name: str, media_path: str, caption: str, hashtags: str, group_urls: Sequence[str],
) -> list[PicMessage]:
    if not group_urls or not page_name.strip() or not media_path.strip() or not (caption.strip() or hashtags.strip()):
        raise ValueError("A task requires a page, media, caption or hashtags, and group links")
    return [
        PicMessage("text", page_name),
        PicMessage("media", media_path=media_path),
        PicMessage("text", caption.strip() + ("\n\n" + hashtags.strip() if hashtags.strip() else "")),
        PicMessage("text", "\n".join(f"{i}. {url}" for i, url in enumerate(group_urls, 1))),
    ]


def confirmation_text(
    done: Collection[int], not_member: Collection[int], removed: Collection[int], blocked: bool, remaining: int,
) -> str:
    if blocked:
        return "Recorded: posting blocked. Group tasks for this page are paused."
    parts = []
    for numbers, prefix, suffix in ((done, "✅ ", ""), (not_member, "", " not joined"), (removed, "", " removed")):
        if numbers:
            parts.append(prefix + ", ".join(map(str, sorted(numbers))) + suffix)
    if not parts:
        return ""
    result = "Recorded " + " · ".join(parts)
    if remaining == 0:
        if not_member or removed:
            result += "."
        result += " Done, thank you!"
    return result
