import pytest

from app.services.fb_share.pic_messages import (
    OFF_TOPIC_TEXT, ONBOARDING_TEXT, build_task_messages, confirmation_text,
)
from app.services.fb_share.types import PicMessage


@pytest.mark.parametrize("caption,hashtags,expected", [
    (" Caption ", " #f1 ", "Caption\n\n#f1"),
    ("Caption", "  ", "Caption"), (" ", " #f1 ", "\n\n#f1"),
])
def test_exact_four_messages(caption, hashtags, expected):
    urls = ["https://facebook.com/groups/a", "https://facebook.com/groups/b"]
    assert build_task_messages("Page", "photo.jpg", caption, hashtags, urls) == [
        PicMessage("text", "Page"), PicMessage("media", media_path="photo.jpg"),
        PicMessage("text", expected),
        PicMessage("text", "1. https://facebook.com/groups/a\n2. https://facebook.com/groups/b"),
    ]
    assert len(urls) == 2


@pytest.mark.parametrize("changes", [
    {"page_name": ""}, {"page_name": " "}, {"media_path": ""}, {"media_path": "\t"},
    {"group_urls": []}, {"caption": " ", "hashtags": "\n"},
])
def test_invalid_task(changes):
    args = dict(page_name="Page", media_path="photo.jpg", caption="Caption", hashtags="", group_urls=["url"])
    args.update(changes)
    with pytest.raises(ValueError):
        build_task_messages(**args)


def test_constants_exact():
    assert ONBOARDING_TEXT == (
        'Studio group tasks.\nEach task: page name, photo or video, caption, then the group links.\n'
        'Post the photo with the caption in each group, about 30 minutes apart.\nReply with the number:\n'
        '"1 sudah" = posted\n"2 bukan anggota" = not a member\n"3 dikeluarkan" = removed from the group\n'
        '"diblokir" = Facebook blocked posting\nYou can also paste the post link.'
    )
    assert OFF_TOPIC_TEXT == 'This number only records Group Share tasks. Reply like "1 sudah" or "2 bukan anggota".'


def test_album_messages_in_order_and_limit():
    media = [f"photo-{i}.jpg" for i in range(12)]
    messages = build_task_messages("Page", media[:3], "Caption", "#f1", ["url"])
    assert messages == [PicMessage("text", "Page"), *[
        PicMessage("media", media_path=path) for path in media[:3]],
        PicMessage("text", "Caption\n\n#f1"), PicMessage("text", "1. url")]
    assert len(build_task_messages("Page", media, "Caption", "", ["url"])) == 13


@pytest.mark.parametrize("done,not_member,removed,blocked,remaining,expected", [
    ({1}, set(), set(), False, 2, "Recorded ✅ 1"),
    ({2}, {3}, set(), False, 0, "Recorded ✅ 2 · 3 not joined. Done, thank you!"),
    (set(), set(), {2}, False, 1, "Recorded 2 removed"),
    ({1}, {2}, {3}, True, 0, "Recorded: posting blocked. Group tasks for this page are paused."),
    ({3, 1}, {5, 4}, {7, 6}, False, 1, "Recorded ✅ 1, 3 · 4, 5 not joined · 6, 7 removed"),
    (set(), set(), set(), False, 1, ""),
])
def test_confirmation(done, not_member, removed, blocked, remaining, expected):
    assert confirmation_text(done, not_member, removed, blocked, remaining) == expected
