"""YouTube subtitle (json3) → word timings + an SRT transcript (Mode 7).

One subtitle download serves both consumers:
  - the AI highlight search reads the SRT (timestamps it can quote back);
  - the burned captions and the clip-boundary snapping use per-word timing.

json3 shape (verified on real tracks, 2026-09-25): `events[]` each with
`tStartMs`, `dDurationMs` and `segs[]`; every seg has `utf8` and — on ASR
(auto-caption) tracks — a `tOffsetMs` relative to its event (the first seg's
offset is implicitly 0). Segments that are just "\\n" are line breaks.
Manually-authored tracks carry no per-word offsets; their words are spread
evenly across the line's duration instead (good enough for captions).
"""

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Line:
    start: float
    end: float
    text: str


def parse_json3(raw: str | bytes) -> tuple[list[Line], list[Word]]:
    data = json.loads(raw)
    lines: list[Line] = []
    words: list[Word] = []
    for ev in data.get("events") or []:
        segs = [s for s in ev.get("segs") or [] if (s.get("utf8") or "").strip()]
        if not segs:
            continue
        ev_start = ev.get("tStartMs", 0) / 1000.0
        ev_end = ev_start + ev.get("dDurationMs", 0) / 1000.0
        text = " ".join(" ".join(s["utf8"].split()) for s in segs).strip()
        lines.append(Line(ev_start, ev_end, text))
        words.extend(_event_words(segs, ev_start, ev_end))
    words.sort(key=lambda w: w.start)
    return _close_gaps(lines), _fix_word_ends(words)


def _event_words(segs: list[dict], ev_start: float, ev_end: float) -> list[Word]:
    has_offsets = any("tOffsetMs" in s for s in segs)
    if has_offsets:
        starts = [ev_start + s.get("tOffsetMs", 0) / 1000.0 for s in segs]
        return [
            Word(st, starts[i + 1] if i + 1 < len(starts) else ev_end, " ".join(s["utf8"].split()))
            for i, (s, st) in enumerate(zip(segs, starts))
        ]
    tokens = " ".join(s["utf8"] for s in segs).split()
    if not tokens:
        return []
    step = max(0.05, (ev_end - ev_start) / len(tokens))
    return [Word(ev_start + i * step, ev_start + (i + 1) * step, t) for i, t in enumerate(tokens)]


def _fix_word_ends(words: list[Word]) -> list[Word]:
    """A word's end never runs past the next word's start (ASR events
    overlap), and never lingers more than 1.2s (a pause after it)."""
    fixed = []
    for i, w in enumerate(words):
        nxt = words[i + 1].start if i + 1 < len(words) else w.end
        end = min(w.end, nxt, w.start + 1.2) if nxt > w.start else min(w.end, w.start + 1.2)
        fixed.append(Word(w.start, max(end, w.start + 0.05), w.text))
    return fixed


def _close_gaps(lines: list[Line]) -> list[Line]:
    """ASR events overlap their successors; trim each to end where the next
    begins so the SRT reads sequentially."""
    out = []
    for i, ln in enumerate(lines):
        end = min(ln.end, lines[i + 1].start) if i + 1 < len(lines) and lines[i + 1].start > ln.start else ln.end
        out.append(Line(ln.start, max(end, ln.start + 0.1), ln.text))
    return out


def _ts(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(lines: list[Line]) -> str:
    return "\n".join(f"{i}\n{_ts(ln.start)} --> {_ts(ln.end)}\n{ln.text}\n" for i, ln in enumerate(lines, 1))


def parse_timestamp(value: str) -> float:
    """"HH:MM:SS,mmm" / "HH:MM:SS.mmm" / "MM:SS" → seconds. Raises ValueError."""
    parts = value.strip().replace(",", ".").split(":")
    if not 2 <= len(parts) <= 3:
        raise ValueError(f"bad timestamp {value!r}")
    secs = float(parts[-1]) + int(parts[-2]) * 60
    if len(parts) == 3:
        secs += int(parts[0]) * 3600
    return secs


def text_between(words: list[Word], start: float, end: float) -> str:
    return " ".join(w.text for w in words if start <= w.start < end)


# A gap this long between two words reads as a natural pause — a safe place
# to start or end a clip without cutting a sentence mid-word.
_PAUSE_S = 0.45


def pause_points(words: list[Word]) -> list[float]:
    """Midpoints of the silent gaps between words, plus sentence ends."""
    points = []
    for a, b in zip(words, words[1:]):
        if b.start - a.end >= _PAUSE_S or a.text.endswith((".", "!", "?")):
            points.append((a.end + b.start) / 2)
    return points


def nearest_pause(points: list[float], t: float, lo: float, hi: float) -> float | None:
    """The pause point closest to `t` within [lo, hi], or None."""
    inside = [p for p in points if lo <= p <= hi]
    return min(inside, key=lambda p: abs(p - t)) if inside else None
