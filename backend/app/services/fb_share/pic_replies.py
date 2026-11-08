import re
from collections.abc import Sequence
from urllib.parse import urlsplit

from .types import ParsedReply


_CATEGORIES = (
    ("not_member", re.compile(r"\b(?:bukan anggota|belum anggota|belum gabung|belum join|not member|not a member)\b", re.I)),
    ("removed", re.compile(r"\b(?:dikeluarkan|di keluarkan|ditendang|removed|kicked)\b", re.I)),
    ("pending", re.compile(r"\b(?:belum|not yet|nanti|later|blm)\b", re.I)),
    ("done", re.compile(r"\b(?:sudah|udah|sdh|done|posted|ok|oke)\b", re.I)),
)
_BLOCKED = re.compile(r"\b(?:diblokir|di blokir|blocked|dibatasi|restricted)\b", re.I)
_POST_URL = re.compile(
    r"\bhttps?://(?:[a-z0-9-]+\.)*facebook\.com/groups/([^/\s?#]+)/"
    r"(?:posts|permalink)/[0-9]+/?(?:\?[^\s<>\"\)\]]*)?", re.I,
)


def parse_reply(text: str, group_urls: Sequence[str]) -> ParsedReply:
    text = text.strip()
    n = len(group_urls)
    matches = list(_POST_URL.finditer(text))
    prose = _POST_URL.sub(" ", text)
    categories: dict[str, set[int]] = {name: set() for name, _ in _CATEGORIES}
    clauses = []
    for clause in re.split(r"[,;\n]|\b(?:dan|and)\b", prose, flags=re.I):
        numbers = {int(value) for value in re.findall(r"\b[0-9]+\b", clause) if 1 <= int(value) <= n}
        category = next((name for name, pattern in _CATEGORIES if pattern.search(clause)), None)
        clauses.append((numbers, category))
    for index, (numbers, category) in enumerate(clauses):
        if category is None:
            category = next((kind for _, kind in clauses[index + 1:] if kind), None)
        if category is None:
            category = next((kind for _, kind in reversed(clauses[:index]) if kind), None)
        if category:
            categories[category].update(numbers)
    if re.search(r"\b(?:semua|all)\b", prose, re.I) and _CATEGORIES[3][1].search(prose):
        categories["done"].update(range(1, n + 1))

    slugs = {urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]: i for i, url in enumerate(group_urls, 1)}
    mentioned = set().union(*(numbers for numbers, _ in clauses))
    post_urls = {}
    for match in matches:
        number = slugs.get(match.group(1))
        if number is None and len(mentioned) == 1:
            number = next(iter(mentioned))
        if number is not None:
            post_urls[number] = "https://" + match.group(0).split("://", 1)[1].rstrip(".,;!?")
            categories["done"].add(number)

    removed = frozenset(categories["removed"])
    not_member = frozenset(categories["not_member"] - removed)
    pending = frozenset(categories["pending"] - removed - not_member)
    done = frozenset(categories["done"] - removed - not_member - pending)
    blocked = bool(_BLOCKED.search(prose))
    return ParsedReply(done, not_member, removed, blocked, post_urls,
                       not (done or not_member or removed or pending or blocked or matches), pending=pending)
