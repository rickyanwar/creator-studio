"""Mode 8 video cover (thumbnail): wording, Flow image prompt and the HTML layout.

Owner decisions (2026-10-21):
- Every brand's covers look like one series: Poppins-Black, uppercase, white fill with a thick black
  stroke, the key part in the fan page's own caption highlight colour, the fan page logo where the
  video overlay puts it. The text is never drawn by the image model.
- The background is a Google Flow text-to-image picture with the same meaning as the video; it does
  not have to be a frame of the video. Its top ~40% stays calm so the title reads.
- The cover is the video's first frame (FB Reels ignores API cover images), shown ~0.3 s then faded
  out over 0.2 s while the narration keeps playing.
"""
from __future__ import annotations

import html
import json
import re

COVER_HOLD_SECONDS = 0.3
COVER_FADE_SECONDS = 0.2
MAX_COVER_LINES = 3
MAX_LINE_CHARS = 13
MAX_COVER_WORDS = 5
MAX_SCENE_CHARS = 400

COVER_COPY_PROMPT = """You write the cover (thumbnail) text for a short vertical explainer video on a {niche} fan page.
The viewer sees the cover for one second while scrolling; the post caption already shows the title.

Video title: {title}
Video caption: {caption}

Write the cover words in the SAME language as the video title.
What works on covers (studies of breakout videos):
- VERY short: 2 to 4 words is best, never more than {max_words}. 1 to 3 short lines, each at most {max_chars} characters.
- Do NOT repeat the title. Add the angle the title does not say: the stakes, the surprising number, the
  result, the contrast, or the question the video answers.
- One strong hook, a little clickbait: it opens a curiosity gap that the video closes.
- Pick the ONE pattern that fits this video best:
  number shock ("125,000 RPM!"), banned/illegal ("BANNED IN 2027"), contrast ("FAST BUT ILLEGAL"),
  question ("WHY IT GROWLS?"), warning ("NEVER IGNORE THIS"), payoff tease ("ONE PULL. CLEAR."),
  hidden detail ("NOBODY SEES THIS").
- Avoid worn-out words: do not use "secret", "insane", "shocking" or "you won't believe".
{avoid_line}- Clickbait never means lying: no claim the video does not support, no invented facts, no fake
  urgency. Use only numbers that appear in the title or caption; keep names, numbers and technical
  terms exactly as written there.
- Never end a line on a small word ("the", "a", "of", "le", "la", "de", "yang", ...); no colon, dash or
  comma at a line end. A final "?" or "!" is fine. No emojis, no hashtags, no quotation marks.
- Highlight the line with the payoff word.

Examples:
"Why 2026 F1 Wings Now Move" -> {{"lines": ["THE WING", "MOVES NOW?!"], "highlight": [1]}}
"Why Your Stomach Growls: The Wave Inside You" -> {{"lines": ["THAT GROWL", "IS A WAVE"], "highlight": [1]}}
"Le turbo : une turbine à 125 000 tours par minute" -> {{"lines": ["125 000", "TOURS/MIN !"], "highlight": [0]}}

Also describe ONE picture for the cover background, in English, for an image model:
- only the hero subject and what it is doing, concrete and visual, matching the title;
- do NOT describe the background, lighting, colours or style (the brand sets those);
- {scene_rule}

Reply with ONLY this JSON:
{{"lines": ["LINE 1", "LINE 2"], "highlight": [index of the highlighted line, 0-based], "scene": "one or two sentences"}}"""

SCENE_RULES = {
    "health": "a clean, realistic 3D anatomy or medical render; no real person's face, no gore.",
    "default": ("a real-looking motorsport scene; a car or bike in a real team's colours is fine, but no "
                "real driver's or rider's face; no series logos."),
}

COVER_IMAGE_PROMPT = """Vertical {aspect} cover image (thumbnail) for a short explainer video titled "{title}".
Scene: {scene}
Style: {style}
Composition: one clear hero subject in the LOWER part of the frame, from about 45% to 90% of the height, sharp and well lit with a strong rim light.
The UPPER 40% of the frame is calm and simple: a soft, out-of-focus background that fades into a smooth, slightly darker gradient toward the top, with no objects, so large title text placed there later stays readable.
High contrast, rich colour, shallow depth of field, physically believable.
Do NOT add any text, letters, numbers, captions, watermarks or logos anywhere in the image. Liveries, signs and screens carry plain colour blocks only, never words or brand names. No series logos."""

STYLES = {
    "health": "soft, clean, realistic 3D anatomy render, warm studio light on a near-white background, medically plausible.",
    "default": "cinematic, realistic photo-quality render like a high-end motorsport broadcast, natural textures, no HDR look.",
}


def _niche_key(niche: str | None) -> str:
    return "health" if (niche or "").strip().lower() == "health" else "default"


def build_cover_copy_prompt(title: str, caption: str | None, niche: str | None,
                            recent_covers: list[str] | None = None) -> str:
    """recent_covers: this fan page's last cover texts, so the hooks do not repeat across videos."""
    recent = [c for c in (recent_covers or []) if c][:8]
    avoid_line = ("- Do not reuse a hook from this page's recent covers: " + "; ".join(recent) + "\n") if recent else ""
    return COVER_COPY_PROMPT.format(
        avoid_line=avoid_line,
        niche=(niche or "sports").strip() or "sports",
        title=(title or "").strip(),
        caption=((caption or "").strip()[:400] or "(none)"),
        max_words=MAX_COVER_WORDS,
        max_chars=MAX_LINE_CHARS,
        scene_rule=SCENE_RULES[_niche_key(niche)],
    )


def fallback_cover_copy(title: str) -> dict:
    """Title split into up to three balanced lines; the last line is highlighted."""
    words = re.sub(r"[\"“”'‘’!?.:;]+$", "", (title or "").strip()).split()
    words = words or ["WATCH"]
    # Balanced greedy split: a line may grow past MAX_LINE_CHARS only when the whole title would not
    # otherwise fit in MAX_COVER_LINES lines (the layout then shrinks the font to fit).
    limit = max(MAX_LINE_CHARS, -(-len(" ".join(words)) // MAX_COVER_LINES) + 2)
    lines: list[str] = []
    for word in words:
        if lines and len(lines[-1]) + 1 + len(word) <= limit:
            lines[-1] = f"{lines[-1]} {word}"
        else:
            lines.append(word)
    if len(lines) > MAX_COVER_LINES:
        lines = lines[:MAX_COVER_LINES - 1] + [" ".join(lines[MAX_COVER_LINES - 1:])]
    lines = [line.upper() for line in lines]
    return {"lines": lines, "highlight": [len(lines) - 1], "scene": ""}


def parse_cover_copy(raw: str, title: str, *, strict: bool = False) -> dict:
    """Validate the model reply. Off-spec replies fall back to the title split, or raise ValueError when
    strict (so news_copywriter._generate_with_fallback tries its next model)."""
    try:
        match = re.search(r"\{.*\}", raw or "", re.DOTALL)
        data = json.loads(match.group(0)) if match else {}
        lines = [re.sub(r"\s+", " ", str(line)).strip().upper() for line in data.get("lines") or []]
        lines = [line for line in lines if line]
        words = sum(len(line.split()) for line in lines)
        if not (1 <= len(lines) <= MAX_COVER_LINES) or words > MAX_COVER_WORDS + 1 or any(
                len(line) > MAX_LINE_CHARS + 4 for line in lines):
            raise ValueError("cover lines off spec")
        raw_highlight = data.get("highlight")
        if not isinstance(raw_highlight, list):
            raw_highlight = [raw_highlight] if raw_highlight is not None else []
        highlight = [int(i) for i in raw_highlight if 0 <= int(i) < len(lines)]
        lines = [re.sub(r"[\s:;,\-–—]+$", "", line) for line in lines]
        scene = re.sub(r"\s+", " ", str(data.get("scene") or "")).strip()[:MAX_SCENE_CHARS]
        return {"lines": lines, "highlight": highlight or [len(lines) - 1], "scene": scene}
    except Exception as exc:
        if strict:
            raise ValueError(f"cover copy off spec: {exc}") from exc
        return fallback_cover_copy(title)


def build_cover_image_prompt(title: str, scene: str | None, niche: str | None, aspect: str = "9:16") -> str:
    key = _niche_key(niche)
    scene = (scene or "").strip() or f"the main subject of the video, shown clearly. {SCENE_RULES[key]}"
    return COVER_IMAGE_PROMPT.format(aspect=aspect, title=(title or "").strip(), scene=scene, style=STYLES[key])


def build_cover_html(*, width: int, height: int, background_src: str, lines: list[str], highlight: list[int],
                     highlight_hex: str, font_src: str, logo_src: str | None = None, logo_text: str | None = None,
                     logo_size_pct: int = 14, logo_position: str = "top_right", logo_margin_pct: int = 3,
                     logo_width_px: int | None = None) -> str:
    """One self-contained page for the renderer's /render-html. Sizes scale with the width so a 720 and a
    1080 wide video get the same look. The logo mirrors ai_video_overlay: ffmpeg scales the logo to
    logo_size_pct % of the LOGO FILE's own width (pass logo_width_px = that result) and places it
    margin % of the video size from the edges, so the cover and the video show the same logo."""
    scale = width / 1080
    side_px = round(70 * scale)
    longest = max((len(line) for line in lines), default=1)
    # Starting size from an estimate (Poppins Black capitals ~0.62 em); the inline script then measures
    # the real lines after the font loads and shrinks until every line fits. The renderer waits for
    # window.__coverReady before taking the screenshot.
    base_px = (150 if len(lines) <= 2 else 132) * scale
    font_px = round(min(base_px, (width - 2 * side_px) / (max(longest, 1) * 0.62)))
    stroke_px = max(4, round(font_px * 0.12))
    top_px = round(height * 0.17)
    margin_x = round(width * logo_margin_pct / 100)
    margin_y = round(height * logo_margin_pct / 100)
    vertical = "bottom" if logo_position.startswith("bottom") else "top"
    horizontal = "right" if logo_position.endswith("right") else "left"
    logo_place = f"{vertical}:{margin_y}px;{horizontal}:{margin_x}px;"
    if logo_src:
        logo_w = logo_width_px or round(width * logo_size_pct / 100)
        logo_html = (f'<img class="logo" style="{logo_place}width:{logo_w}px" '
                     f'src="{html.escape(logo_src, quote=True)}">')
    elif logo_text:
        # Mirrors the drawtext fallback in ai_video_overlay: 34 px, white at 85%, 2 px black shadow.
        logo_html = (f'<div class="logo txt" style="{logo_place}font-size:34px;color:rgba(255,255,255,.85);'
                     f'text-shadow:2px 2px 0 rgba(0,0,0,.6);line-height:1">{html.escape(logo_text)}</div>')
    else:
        logo_html = ""
    title_html = "".join(
        f'<div class="{"hi" if i in highlight else ""}">{html.escape(line)}</div>' for i, line in enumerate(lines)
    )
    color = highlight_hex if re.fullmatch(r"#[0-9A-Fa-f]{6}", highlight_hex or "") else "#FFE500"
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
@font-face {{ font-family: CoverFont; src: url("{font_src}"); }}
html, body {{ margin: 0; width: {width}px; height: {height}px; overflow: hidden; background: #000; }}
.bg {{ position: absolute; inset: 0; background: url("{background_src}") center / cover no-repeat; }}
.shade {{ position: absolute; left: 0; right: 0; top: 0; height: 55%;
  background: linear-gradient(rgba(0,0,0,.45), rgba(0,0,0,0)); }}
.logo {{ position: absolute; }}
.logo.txt {{ font-family: CoverFont; }}
.title {{ position: absolute; left: {side_px}px; right: {side_px}px; top: {top_px}px; text-align: center;
  font-family: CoverFont; font-size: {font_px}px; line-height: 1.04; color: #fff; text-transform: uppercase;
  -webkit-text-stroke: {stroke_px}px #000; paint-order: stroke fill; letter-spacing: -1px;
  filter: drop-shadow(0 {round(10 * scale)}px {round(18 * scale)}px rgba(0,0,0,.55)); }}
.title div {{ white-space: nowrap; }}
.title .hi {{ color: {color}; }}
</style></head><body><div class="bg"></div><div class="shade"></div>{logo_html}
<div class="title" id="t">{title_html}</div>
<script>
document.fonts.ready.then(function () {{
  var t = document.getElementById("t"), size = {font_px}, floor = {round(48 * scale)};
  function overflows() {{
    for (var i = 0; i < t.children.length; i++) if (t.children[i].scrollWidth > t.clientWidth) return true;
    return false;
  }}
  while (overflows() && size > floor) {{
    size -= 2; t.style.fontSize = size + "px"; t.style.webkitTextStroke = Math.max(4, Math.round(size * 0.12)) + "px #000";
  }}
  window.__coverReady = true;
}});
</script></body></html>"""
