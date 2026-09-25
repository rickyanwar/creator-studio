"""Word-by-word burned captions for Mode 7 clips (an ASS file for libass).

Style ported from jipraks/yt-short-clipper's caption_generator.py (MIT ©
2026 Aji Prakoso; see services/yt_highlights.py for the full notice): a few
upper-case words at a time, the word being spoken highlighted, heavy
outline. Changes: no flicker (each word's event runs until the next word
starts, and a chunk stays up across short pauses), chunks break at sentence
ends and long pauses, ASR noise tags ("[music]", "[ __ ]") are dropped, and
the font is the bundled Poppins Black (app/assets/fonts — Linux has no Arial
Black; pass that directory to ffmpeg's `ass` filter as fontsdir).

Captions stay in the video's own language — they're the speaker's words.
"""

import re
from pathlib import Path

from app.services.yt_transcript import Word

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_FONT_NAME = "Poppins Black"
_WIDTH, _HEIGHT = 1080, 1920
_FONT_SIZE = 76
_OUTLINE = 6
_SHADOW = 2
_MARGIN_SIDE = 70
_MARGIN_V = 560             # bottom of the text block at ~71% of the height, clear of the Reels UI
_HIGHLIGHT = "&H0000E5FF&"  # BGR — warm yellow
_WORDS_PER_CHUNK = 3
_MAX_CHUNK_CHARS = 22
_CHUNK_BREAK_PAUSE = 0.7    # a longer silence than this starts a new chunk
_HOLD_ACROSS_GAP = 0.6      # keep the chunk on screen across gaps shorter than this

_NOISE_RE = re.compile(r"^\[[^\]]*\]$")
_ASS_UNSAFE_RE = re.compile(r"[{}\\]")


def _clean(text: str) -> str:
    return _ASS_UNSAFE_RE.sub("", text).strip()


def _ass_time(seconds: float) -> str:
    cs = int(round(max(0.0, seconds) * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6_000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


def clip_words(words: list[Word], start: float, end: float) -> list[Word]:
    """The clip's words, re-timed to start at 0, noise tags dropped."""
    out = []
    for w in words:
        if w.end <= start or w.start >= end:
            continue
        text = _clean(w.text)
        if not text or _NOISE_RE.match(text):
            continue
        out.append(Word(max(0.0, w.start - start), min(end, w.end) - start, text))
    return out


def chunk_words(words: list[Word]) -> list[list[Word]]:
    chunks: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            too_long = len(" ".join(x.text for x in current + [w])) > _MAX_CHUNK_CHARS
            paused = w.start - current[-1].end > _CHUNK_BREAK_PAUSE
            ended = current[-1].text.endswith((".", "!", "?"))
            if len(current) >= _WORDS_PER_CHUNK or too_long or paused or ended:
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def build_ass(words: list[Word], clip_duration: float) -> str:
    """ASS script for an already re-timed (clip_words) word list."""
    header = f"""[Script Info]
ScriptType: v4.00+
WrapStyle: 0
PlayResX: {_WIDTH}
PlayResY: {_HEIGHT}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{_FONT_NAME},{_FONT_SIZE},&H00FFFFFF,&H000000FF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,{_OUTLINE},{_SHADOW},2,{_MARGIN_SIDE},{_MARGIN_SIDE},{_MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    chunks = chunk_words(words)
    for ci, chunk in enumerate(chunks):
        next_chunk_start = chunks[ci + 1][0].start if ci + 1 < len(chunks) else clip_duration
        for wi, w in enumerate(chunk):
            if wi + 1 < len(chunk):
                end = chunk[wi + 1].start
            elif next_chunk_start - w.end < _HOLD_ACROSS_GAP:
                end = next_chunk_start
            else:
                end = w.end + 0.25
            end = min(max(end, w.start + 0.05), clip_duration)
            text = " ".join(
                f"{{\\c{_HIGHLIGHT}}}{x.text.upper()}{{\\c&H00FFFFFF&}}" if xi == wi else x.text.upper()
                for xi, x in enumerate(chunk)
            )
            events.append(f"Dialogue: 0,{_ass_time(w.start)},{_ass_time(end)},Default,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


def write_ass(words: list[Word], start: float, end: float, path: Path) -> bool:
    """Write the clip's caption file. False when the clip has no spoken words
    (the render then just skips captions)."""
    local = clip_words(words, start, end)
    if not local:
        return False
    path.write_text(build_ass(local, end - start), encoding="utf-8")
    return True
