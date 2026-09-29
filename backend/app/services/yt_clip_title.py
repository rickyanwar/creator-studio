"""Title and caption validators for Mode 7 (YouTube clips) — S2f / S12.

These are pure functions with no I/O, intentionally framework-free so they
are easy to unit-test and to call from both yt_clip.py and yt_highlights.py.

  clean_clip_title(title, people, forbidden_channel) -> str | None
      Enforce S2f title rules. Returns the cleaned title or None if the clip
      should be dropped (no people, or name won't fit in 60 chars).

  strip_source_lines(caption, channel_name) -> str
      Remove "Source: …" / "Sumber: …" / "via …" / "@handle" / URLs / channel
      name lines from a generated caption (Mode 7 only).

  one_line_caption(caption, max_body) -> str   [S12]
      Collapse any whitespace/newlines to a single line, separate body text
      from trailing hashtags, trim the body to max_body if needed, and return
      "body tags" (or just "body" when there are no tags).
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_TITLE_LEN = 60

# Patterns that indicate a source/credit line at the start of a word.
# NOTE: "via" is intentionally excluded here — it is only a source marker
# when it appears as a TRAILING attribution clause (e.g. "Title — via Channel")
# or at the very start of the title.  Mid-sentence uses like "wins via strategy"
# must not be stripped.  See _TITLE_VIA_RE below for the targeted pattern.
_SOURCE_PREFIX_PATTERNS = re.compile(
    r"\b(source|sumber|credit|kredit|©|copyright)\b",
    re.IGNORECASE,
)

# "via" as a source marker in a TITLE — only when it:
#   • appears at the start of the title ("via Channel Name"), OR
#   • follows a separator (—, –, -, |, or an opening parenthesis) as a trailing
#     attribution clause, optionally with surrounding whitespace.
# Does NOT match mid-sentence "via" (e.g. "Verstappen wins via strategy call").
_TITLE_VIA_RE = re.compile(
    r"(?:^via\s+\S.*$|(?:—|–|-+|\||\()\s*via\b.*$)",
    re.IGNORECASE,
)

# Match @handles (Twitter/IG style).
_HANDLE_RE = re.compile(r"@\w+")

# Match bare URLs — http/https or www. prefixed.
_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

# Two-part title patterns: "X — Y", "X - Y", "X: Y"
# The separators can have surrounding spaces.
_TWO_PART_RE = re.compile(r"\s*(?:—|–|-{1,2}|:)\s+")

# Patterns in a caption line that should cause the whole line to be stripped.
_CAPTION_SOURCE_RE = re.compile(
    r"^\s*(?:source|sumber|credit|kredit|via)[:\s]",
    re.IGNORECASE,
)


def _normalise_ws(text: str) -> str:
    """Collapse internal whitespace to single spaces and strip edges."""
    return " ".join(text.split())


def _contains_name(title_lower: str, people: list[str]) -> bool:
    """Return True if the title (lower-cased) contains at least one full name,
    surname, or name token of length >= 3 from `people` (case-insensitive,
    whole-word match).

    S10(b): extend matching to include any name token (first name, nickname-like
    tokens) of length >= 3, whole-word, case-insensitive.  Only when NONE of
    these match should the caller prefix the surname.

    Prod example: people=["Kimi Antonelli"], title "Kimi Told to Save Fuel or
    Risk a DNF" — "Kimi" is a name token of length 4 >= 3, so the title is
    accepted without prefixing.
    """
    for person in people:
        parts = person.strip().split()
        if not parts:
            continue
        # Full name match (whole-string, case-insensitive already handled by caller)
        if person.lower() in title_lower:
            return True
        # Surname-only whole-word match (last word of the full name)
        surname = parts[-1].lower()
        if surname and re.search(r"\b" + re.escape(surname) + r"\b", title_lower):
            return True
        # Any name token of length >= 3 — whole-word match
        for token in parts:
            token_lower = token.lower()
            if len(token_lower) >= 3 and re.search(
                r"\b" + re.escape(token_lower) + r"\b", title_lower
            ):
                return True
    return False


def _has_source_content(text: str) -> bool:
    """True if text contains a source/credit indicator or a URL/@handle.

    Used for TITLE cleaning only.  "via" is only flagged when it appears as a
    leading or trailing attribution clause — not mid-sentence.
    """
    return bool(
        _SOURCE_PREFIX_PATTERNS.search(text)
        or _TITLE_VIA_RE.search(text)
        or _HANDLE_RE.search(text)
        or _URL_RE.search(text)
    )


def _pick_strongest_part(title: str, people: list[str] | None = None) -> str:
    """If the title is a 'X — Y' / 'X - Y' / 'X: Y' two-part form, keep the
    strongest part.

    Priority (in order):
    1. The half that contains a name from `people` (if people is supplied).
    2. The longer half.
    3. If equal length, keep the first half (the hook).
    """
    parts = _TWO_PART_RE.split(title, maxsplit=1)
    if len(parts) < 2:
        return title
    a, b = parts[0].strip(), parts[1].strip()
    # Priority 1: prefer the half that contains a name.
    if people:
        a_has = _contains_name(a.lower(), people)
        b_has = _contains_name(b.lower(), people)
        if b_has and not a_has:
            return b
        if a_has and not b_has:
            return a
    # Priority 2: keep the longer half; if equal, prefer the first (the hook).
    return b if len(b) > len(a) else a


def clean_clip_title(
    title: str,
    people: list[str],
    forbidden_channel: str | None = None,
) -> str | None:
    """Apply S2f title rules and return the cleaned title, or None to drop the clip.

    Rules applied (in order):
    1. Normalise whitespace.
    2. Strip two-part "X — Y" / "X - Y" / "X: Y" patterns → keep strongest part.
    3. Reject / strip source markers (Source, Sumber, Credit, via, ©, @handle, URL).
    4. Strip the YouTube channel name if it appears (forbidden_channel).
    5. Must contain a name from `people`; if missing and people is non-empty,
       prefix with the main person's surname — only if the result fits ≤ 60 chars,
       else return None (drop the clip).
    6. If `people` is empty → return None (drop the clip).
    7. Enforce max 60 chars.
    """
    if not title:
        return None

    # Step 1: normalise
    title = _normalise_ws(title)

    # Step 2: flatten two-part titles (prefer the half containing a name)
    title = _pick_strongest_part(title, people=people)
    title = _normalise_ws(title)

    # Step 3: reject any title that contains source/credit/URL/@handle markers.
    # We strip the forbidden content rather than dropping the clip outright —
    # but if what remains is too short or still violates rules, we return None.
    if _has_source_content(title):
        # Attempt to strip just the offending tokens — often the attribution is
        # appended after a separator.
        # Remove handles and URLs inline.
        title = _HANDLE_RE.sub("", title)
        title = _URL_RE.sub("", title)
        # Remove source/credit words (source|sumber|credit|kredit|©|copyright).
        title = _SOURCE_PREFIX_PATTERNS.sub("", title)
        # Remove trailing "via …" / "— via …" attribution clauses.
        title = _TITLE_VIA_RE.sub("", title)
        title = _normalise_ws(title)
        # If source markers appeared, also strip any trailing dash/colon left over.
        title = title.rstrip(":-–—").strip()
        title = _normalise_ws(title)
        if _has_source_content(title):
            logger.info("clean_clip_title: dropping — source markers remain after strip: %r", title)
            return None

    # Step 4: strip the YouTube channel name if present.
    if forbidden_channel:
        # Case-insensitive word-boundary removal.
        escaped = re.escape(forbidden_channel.strip())
        title = re.sub(escaped, "", title, flags=re.IGNORECASE).strip()
        title = title.rstrip(":-–— ()").strip()
        title = _normalise_ws(title)

    if not title:
        return None

    # Step 5 + 6: people requirement.
    if not people:
        logger.info("clean_clip_title: dropping — people list is empty")
        return None

    title_lower = title.lower()
    if not _contains_name(title_lower, people):
        # Try to prefix with the main person's surname.
        main_surname = people[0].strip().split()[-1]
        candidate = _normalise_ws(f"{main_surname} {title}")
        if len(candidate) <= _MAX_TITLE_LEN:
            title = candidate
            logger.debug("clean_clip_title: prefixed surname %r → %r", main_surname, title)
        else:
            logger.info(
                "clean_clip_title: dropping — name %r missing and surname prefix doesn't fit (%d chars): %r",
                people[0], len(candidate), candidate,
            )
            return None

    # Step 7: hard cap.
    if len(title) > _MAX_TITLE_LEN:
        logger.info(
            "clean_clip_title: dropping — title too long (%d > %d): %r",
            len(title), _MAX_TITLE_LEN, title,
        )
        return None

    return title


# ── clean_clip_hashtags ───────────────────────────────────────────────────────

# Explicit denylist of forbidden tag stems (without '#', lower-cased).
_FORBIDDEN_TAG_STEMS: frozenset[str] = frozenset({
    # Game / platform titles
    "f126", "f125", "f124", "f123", "f122", "f121", "f120",
    "f1game", "easportsf1", "gaming", "gameplay", "simracing",
    "esports", "esport", "ps5", "ps4", "xbox", "pc", "playstation",
    # Generic engagement
    "fyp", "viral", "reels", "foryou", "trending", "explore", "tiktok",
    "follow", "like", "share", "subscribe",
})

# Regex: #F1_?NN style game-year tags (e.g. #F126, #F1_26, #F125)
_GAME_YEAR_RE = re.compile(r"^f1_?\d{2,}$", re.IGNORECASE)

# Substrings that, if present anywhere in a tag stem, mark it as forbidden.
_FORBIDDEN_SUBSTRINGS: tuple[str, ...] = (
    "game", "gaming", "gameplay", "esport", "simracing",
)

# Regex to find all hashtag tokens anywhere in a string.
# Matches '#' followed by one or more word characters.
_HASHTAG_RE = re.compile(r"#(\w+)")

# A "hashtag line" is a line whose non-space content is entirely hashtags.
_HASHTAG_LINE_RE = re.compile(r"^\s*(#\w+(\s+#\w+)*)\s*$")


def _is_forbidden_tag(stem: str) -> bool:
    """Return True if the tag stem (without '#', any case) should be removed."""
    low = stem.lower()
    if low in _FORBIDDEN_TAG_STEMS:
        return True
    if _GAME_YEAR_RE.match(low):
        return True
    for sub in _FORBIDDEN_SUBSTRINGS:
        if sub in low:
            return True
    return False


def _squash(text: str) -> str:
    """Lower-case and remove all spaces/punctuation → canonical form for comparison."""
    return re.sub(r"[\s\W]+", "", text).lower()


def _niche_to_hashtag(niche: str) -> str:
    """Convert a niche label to a #Hashtag (strip spaces/punct, title-case first char)."""
    stem = re.sub(r"[\s\W]+", "", niche)
    return "#" + stem if stem else ""


def _split_inline_tags(line: str) -> tuple[str, list[str]]:
    """Split a line into (body_part, trailing_hashtag_stems).

    A "trailing hashtag run" is the contiguous sequence of whitespace-separated
    tokens at the END of the line where every token matches ``#word``.  Tokens
    that look like ``#1`` (digit-only stem, length 1) inside a sentence such as
    "the #1 fan" are still captured here because they form part of the trailing
    run — the caller is responsible for not treating them as hashtag tokens when
    they are *not* at the end.

    If the whole line is a pure-hashtag line the body_part is empty string.
    If there are no trailing hashtag tokens the body_part is the full line and
    the list is empty.
    """
    tokens = line.split()
    split_idx = len(tokens)
    for i in range(len(tokens) - 1, -1, -1):
        if _TRAILING_HASHTAG_TOKEN_RE.match(tokens[i]):
            split_idx = i
        else:
            break
    tag_stems = [t.lstrip("#") for t in tokens[split_idx:]]
    body_part = " ".join(tokens[:split_idx])
    return body_part, tag_stems


def clean_clip_hashtags(
    caption: str,
    niche: str | None,
    channel_name: str | None,
) -> str:
    """Remove forbidden hashtags from *caption* and ensure the niche tag is present.

    Rules (S7):
    • Case-insensitive removal of all forbidden tags (denylist + game-year regex
      + any stem containing game/gaming/gameplay/esport/simracing).
    • The YouTube channel name (squashed, no spaces) is also forbidden.
    • Deduplicate surviving tags (case-insensitive, keep first occurrence).
    • If no tag equal to the niche tag remains, append it on the hashtag line.
    • All hashtags are kept on ONE final line; the rest of the body is untouched.
    • '#' characters that appear inside normal sentences (no word-boundary after
      '#') are NOT touched — only tokens matching #word are processed.

    One-line caption support (S12 fix):
    • When the last non-empty line contains body text followed by hashtag tokens
      (e.g. "Kimi hits P4 — stay out? #F1 #KimiAntonelli #AzerbaijanGP"), the
      trailing hashtag run is extracted from that line and treated exactly like a
      standalone hashtag block.  The body portion of that line is kept as-is.
      This prevents the niche tag from being double-appended when the AI already
      wrote a correct one-liner.

    Returns the cleaned caption.
    """
    if not caption:
        return caption

    # Compute forbidden squashed forms for channel name.
    forbidden_squashed: set[str] = set()
    if channel_name:
        forbidden_squashed.add(_squash(channel_name))

    # --- Split caption body from trailing hashtag line(s) ---
    # Walk backwards:
    #   • Skip blank lines.
    #   • Collect pure-hashtag lines into the tag block.
    #   • On the first non-blank, non-pure-hashtag line: check whether its
    #     *trailing tokens* form a hashtag run.  If so, split that line —
    #     the trailing run joins the tag block, the remainder stays as body.
    lines = caption.splitlines()

    body_lines: list[str] = []
    tag_block_stems: list[str] = []   # stems (without '#') in order
    i = len(lines) - 1
    while i >= 0:
        stripped = lines[i].strip()
        if stripped == "":
            i -= 1
            continue
        if _HASHTAG_LINE_RE.match(lines[i]):
            # Pure-hashtag line — collect its stems and keep scanning back.
            stems = _HASHTAG_RE.findall(lines[i])
            tag_block_stems[:0] = stems   # prepend (preserve order)
            i -= 1
        else:
            # Mixed or plain body line — check for an inline trailing hashtag run.
            body_part, inline_stems = _split_inline_tags(stripped)
            if inline_stems:
                # Replace the line with just the body part; collect the inline tags.
                tag_block_stems[:0] = inline_stems
                # Replace this line in-place: use the body part (may be empty).
                if body_part:
                    lines[i] = body_part
                else:
                    # Whole line was hashtags — treat as pure-hashtag line and drop.
                    lines[i] = ""
            # Stop scanning; everything above this line is body.
            break
    body_lines = lines[: i + 1]

    # Filter forbidden tags; deduplicate case-insensitively.
    seen_lower: set[str] = set()
    kept_stems: list[str] = []
    for stem in tag_block_stems:
        if _is_forbidden_tag(stem):
            continue
        low = stem.lower()
        sq = _squash(stem)
        if sq in forbidden_squashed:
            continue
        if low in seen_lower:
            continue
        seen_lower.add(low)
        kept_stems.append(stem)

    # Ensure niche tag is present.
    niche_tag_stem = ""
    if niche:
        niche_tag = _niche_to_hashtag(niche)
        niche_tag_stem = niche_tag.lstrip("#")
        niche_sq = _squash(niche_tag_stem)
        if niche_tag_stem and niche_sq not in {_squash(s) for s in kept_stems}:
            kept_stems.append(niche_tag_stem)

    # Rebuild: body + blank line (if body non-empty) + single hashtag line.
    hashtag_line = " ".join(f"#{s}" for s in kept_stems)

    result_lines = [l for l in body_lines]
    # Remove trailing blank lines from body before appending tag line.
    while result_lines and result_lines[-1].strip() == "":
        result_lines.pop()

    if hashtag_line:
        result_lines.append("")  # blank separator
        result_lines.append(hashtag_line)

    return "\n".join(result_lines)


def strip_source_lines(caption: str, channel_name: str | None = None) -> str:
    """Remove source/credit lines from a Mode 7 caption.

    Strips any line that:
      • starts with Source:/Sumber:/Credit:/Kredit:/via
      • contains a @handle or a bare URL
      • contains "(YouTube)" (case-insensitive)
      • equals the YouTube channel name (after normalisation), if supplied

    Returns the cleaned caption with trailing blank lines removed.
    """
    if not caption:
        return caption

    channel_lower = (channel_name or "").strip().lower()
    out_lines: list[str] = []
    for line in caption.splitlines():
        stripped = line.strip()
        # Source/credit prefix
        if _CAPTION_SOURCE_RE.match(stripped):
            continue
        # "@handle" anywhere on the line
        if _HANDLE_RE.search(stripped):
            continue
        # URL anywhere on the line
        if _URL_RE.search(stripped):
            continue
        # "(YouTube)" mention
        if re.search(r"\(youtube\)", stripped, re.IGNORECASE):
            continue
        # Channel name as its own line (or after "Source: …")
        if channel_lower and stripped.lower() == channel_lower:
            continue
        # "via <channel>" or "Source: <channel>"
        if channel_lower and channel_lower in stripped.lower() and _SOURCE_PREFIX_PATTERNS.search(stripped):
            continue
        out_lines.append(line)

    return "\n".join(out_lines).rstrip()


# ── one_line_caption (S12) ────────────────────────────────────────────────────

# Matches a '#' followed by word characters that appears as a token at the END
# of a whitespace-delimited sequence.  We collect trailing hashtag tokens after
# splitting the flat text.
_TRAILING_HASHTAG_TOKEN_RE = re.compile(r"^#\w+$")


def one_line_caption(caption: str, max_body: int = 180) -> str:
    """Collapse a multi-paragraph caption to a single line (S12).

    Algorithm:
    1. Collapse all whitespace / newlines → single spaces, strip edges.
    2. Separate trailing hashtag tokens (tokens starting with '#' at the END
       of the token sequence, contiguous from the right) from body tokens.
       A '#' that is embedded inside a word (e.g. "#1" in "the #1 fan of …")
       is only a "hashtag token" when the whole whitespace-separated token
       starts with '#' and the token is in the trailing hashtag run.
    3. If body exceeds max_body:
       a. Try to cut at the last sentence-end punctuation ('.', '!', '?') that
          keeps ≥ 60 % of max_body.
       b. Otherwise cut at the last word boundary and append '…'.
    4. Return f"{body} {tags}".strip().  If there are no tags, return body
       only (no trailing space).

    Does NOT add or remove hashtags — use clean_clip_hashtags for that.
    """
    if not caption:
        return caption

    # Step 1: flatten all whitespace.
    flat = _normalise_ws(caption)

    # Step 2: find the trailing hashtag token run.
    tokens = flat.split(" ")
    split_idx = len(tokens)  # index of first hashtag token in trailing run
    for i in range(len(tokens) - 1, -1, -1):
        if _TRAILING_HASHTAG_TOKEN_RE.match(tokens[i]):
            split_idx = i
        else:
            break

    body_tokens = tokens[:split_idx]
    tag_tokens = tokens[split_idx:]

    body = " ".join(body_tokens)
    tags = " ".join(tag_tokens)

    # Step 3: trim body if it exceeds max_body.
    if len(body) > max_body:
        threshold = int(max_body * 0.6)
        # (a) Last sentence-end punctuation within [threshold, max_body].
        cut_pos = -1
        for i in range(max_body - 1, threshold - 1, -1):
            if i < len(body) and body[i] in ".!?":
                cut_pos = i + 1  # include the punctuation
                break
        if cut_pos != -1:
            body = body[:cut_pos].rstrip()
        else:
            # (b) Cut at the last word boundary within max_body - 1
            #     (leaving room for the ellipsis character).
            candidate = body[: max_body - 1]  # '…' is one character
            last_space = candidate.rfind(" ")
            if last_space > 0:
                body = body[:last_space].rstrip() + "…"
            else:
                body = candidate + "…"

    # Step 4: assemble.
    if tags:
        return body + " " + tags
    return body
