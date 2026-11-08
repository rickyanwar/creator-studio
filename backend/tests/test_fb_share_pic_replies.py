import pytest

from app.services.fb_share.pic_replies import parse_reply


URLS = ["https://facebook.com/groups/alpha/", "https://facebook.com/groups/beta", "https://facebook.com/groups/123"]


@pytest.mark.parametrize("text,done,not_member,removed,blocked,help_expected", [
    ("1 sudah", {1}, set(), set(), False, False),
    ("1 2 sudah", {1, 2}, set(), set(), False, False),
    ("1, 2 sudah", {1, 2}, set(), set(), False, False),
    ("semua sudah", {1, 2, 3}, set(), set(), False, False),
    ("ALL DONE", {1, 2, 3}, set(), set(), False, False),
    ("2 bukan anggota", set(), {2}, set(), False, False),
    ("2 sudah, 3 bukan anggota", {2}, {3}, set(), False, False),
    ("3 dikeluarkan", set(), set(), {3}, False, False),
    ("diblokir", set(), set(), set(), True, False),
    (" OK 1 ", {1}, set(), set(), False, False),
    ("7 sudah", set(), set(), set(), False, True),
    ("can you make me a caption", set(), set(), set(), False, True),
    ("1", set(), set(), set(), False, True),
    ("1 sudah; 2\n3", {1, 2, 3}, set(), set(), False, False),
    ("1 dan 2 and 3 posted", {1, 2, 3}, set(), set(), False, False),
    ("1 sudah, 2, 3 bukan anggota", {1}, {2, 3}, set(), False, False),
    ("1 done, 1 not member, 1 removed", set(), set(), {1}, False, False),
    ("1 done, 1 not a member", set(), {1}, set(), False, False),
    ("1 bukan anggota ok", set(), {1}, set(), False, False),
    ("someone said okayish 1", set(), set(), set(), False, True),
    ("", set(), set(), set(), False, True),
])
def test_reply_rules(text, done, not_member, removed, blocked, help_expected):
    result = parse_reply(text, URLS)
    assert result.done == frozenset(done)
    assert result.not_member == frozenset(not_member)
    assert result.removed == frozenset(removed)
    assert result.blocked is blocked
    assert result.is_help is help_expected


@pytest.mark.parametrize("keyword", ["sudah", "udah", "sdh", "done", "posted", "ok", "oke"])
def test_done_keywords(keyword):
    assert parse_reply(f"1 {keyword}", URLS).done == {1}


@pytest.mark.parametrize("keyword", ["bukan anggota", "belum anggota", "belum gabung", "belum join", "not member", "not a member"])
def test_not_member_keywords(keyword):
    assert parse_reply(f"2 {keyword}", URLS).not_member == {2}


@pytest.mark.parametrize("keyword", ["dikeluarkan", "di keluarkan", "ditendang", "removed", "kicked"])
def test_removed_keywords(keyword):
    assert parse_reply(f"3 {keyword}", URLS).removed == {3}


@pytest.mark.parametrize("keyword", ["diblokir", "di blokir", "blocked", "dibatasi", "restricted"])
def test_blocked_keywords(keyword):
    assert parse_reply(keyword, URLS).blocked


@pytest.mark.parametrize("text,expected", [
    ("http://m.facebook.com/groups/alpha/posts/987", {1: "https://m.facebook.com/groups/alpha/posts/987"}),
    ("https://www.facebook.com/groups/beta/permalink/987/", {2: "https://www.facebook.com/groups/beta/permalink/987/"}),
    ("2 https://facebook.com/groups/unknown/posts/987", {2: "https://facebook.com/groups/unknown/posts/987"}),
    ("https://facebook.com/groups/123/posts/1", {3: "https://facebook.com/groups/123/posts/1"}),
    ("1 2 https://facebook.com/groups/unknown/posts/987", {}),
    ("https://facebook.com.evil/groups/alpha/posts/1", {}),
    ("https://notfacebook.com/groups/alpha/posts/1", {}),
    ("(https://facebook.com/groups/alpha/posts/987).", {1: "https://facebook.com/groups/alpha/posts/987"}),
])
def test_post_urls(text, expected):
    result = parse_reply(text, URLS)
    assert result.post_urls == expected
    assert result.done == frozenset(expected)


def test_url_conflict_and_multiple_urls():
    result = parse_reply("1 removed, https://facebook.com/groups/alpha/posts/9 and https://facebook.com/groups/beta/posts/8", URLS)
    assert result.removed == {1}
    assert result.done == {2}
    assert set(result.post_urls) == {1, 2}
    assert not result.is_help


def test_empty_groups_and_numbers_in_url_do_not_become_fallback():
    assert parse_reply("semua sudah", []).done == frozenset()
    result = parse_reply("https://facebook.com/groups/unknown/posts/2", URLS)
    assert not result.done
    assert not result.post_urls
    assert not result.is_help


@pytest.mark.parametrize("keyword", ["belum", "not yet", "nanti", "later", "blm"])
def test_pending_keywords_do_not_mark_done(keyword):
    result = parse_reply(f"1 {keyword} done, 2 sudah", URLS)
    assert result.pending == {1}
    assert result.done == {2}
    assert not result.not_member
    assert not result.removed
    assert not result.is_help


def test_pending_numbers_are_consumed_without_affecting_neighbours():
    result = parse_reply("1 belum, 2 sudah, 3 nanti", URLS)
    assert result.pending == {1, 3}
    assert result.done == {2}
    result = parse_reply("1 belum", URLS)
    assert result.pending == {1}
    assert not result.done
    assert not result.is_help


@pytest.mark.parametrize("text,not_member,removed", [
    ("1 belum anggota", {1}, set()), ("1 belum gabung", {1}, set()),
    ("1 belum join", {1}, set()), ("1 removed later", set(), {1}),
])
def test_membership_categories_take_priority_over_pending(text, not_member, removed):
    result = parse_reply(text, URLS)
    assert result.not_member == not_member
    assert result.removed == removed
    assert not result.pending


def test_parsed_reply_pending_defaults_empty():
    from app.services.fb_share.types import ParsedReply

    result = ParsedReply(frozenset(), frozenset(), frozenset(), False, {}, True)
    assert result.pending == frozenset()
