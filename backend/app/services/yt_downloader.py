"""yt-dlp wrapper for Mode 7 — the only module that talks to YouTube's player.

Verified from the VPS (2026-09-25): metadata, subtitle and section downloads
all work WITHOUT cookies or a proxy. Both stay optional Settings fallbacks
for when YouTube starts bot-walling the datacenter IP — and when it does,
the circuit breaker below pauses every YouTube task for an hour (with the
reason shown in Settings) instead of hammering it.

Two rules learned the hard way in the spike:
  - Only ever request the ORIGINAL subtitle track (manual `<lang>` or the
    ASR `<lang>-orig`). Asking YouTube for a machine-translated track (e.g.
    `id`) got HTTP 429 immediately. Translation happens in 9Router.
  - One metadata extraction, then exactly one subtitle request through the
    same YoutubeDL session (its cookies/proxy/headers) — no second player
    extraction per video.
"""

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_BLOCK_PAUSE = timedelta(hours=1)
_BLOCK_HINTS = (
    "sign in to confirm", "not a bot", "http error 403", "http error 429", "too many requests",
    "confirm you're not a bot", "confirm you’re not a bot", "please sign in",
)
_UNAVAILABLE_HINTS = (
    "private video", "video unavailable", "has been removed", "confirm your age", "age-restricted",
    "members-only", "join this channel", "this live event will begin", "premieres in",
    "is not available in your country", "copyright claim",
)
_SECTION_FORMAT = "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba/b[height<=1080]/b"
_FULL_DOWNLOAD_MAX_S = 20 * 60   # fallback when a section download fails
_SECTION_PAD_BEFORE = 1.5
_SECTION_PAD_AFTER = 2.5


class YouTubeBlockedError(RuntimeError):
    """YouTube is refusing this IP/account — pause all YouTube work."""


class YouTubeUnavailableError(RuntimeError):
    """This particular video can't be processed (private, removed, age/member
    gated, not yet live) — skip it, don't retry."""


@dataclass(frozen=True)
class VideoMeta:
    video_id: str
    title: str
    channel: str
    duration_s: int | None
    live_status: str | None      # not_live | is_live | is_upcoming | was_live | post_live
    published_at: datetime | None
    language: str | None
    sub_track: str | None        # e.g. "en-orig" / "en"; None → no usable subtitles


@dataclass(frozen=True)
class Section:
    path: Path
    offset_s: float              # where in the video this file starts


def watch_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


# ── circuit breaker ──────────────────────────────────────────────────────────

def _settings_row(db):
    from app.models.settings import Settings

    return db.query(Settings).filter_by(id=1).first()


def blocked_until(db) -> datetime | None:
    row = _settings_row(db)
    until = row.youtube_blocked_until if row else None
    return until if until and until > datetime.now(timezone.utc).replace(tzinfo=None) else None


def ensure_not_blocked(db) -> None:
    until = blocked_until(db)
    if until:
        raise YouTubeBlockedError(f"YouTube access paused until {until:%Y-%m-%d %H:%M} UTC")


def _mark_blocked(db, message: str) -> None:
    row = _settings_row(db)
    if not row:
        return
    row.youtube_blocked_until = datetime.now(timezone.utc).replace(tzinfo=None) + _BLOCK_PAUSE
    row.youtube_last_error = message[:2000]
    db.commit()
    logger.error("YouTube blocked us — pausing all YouTube work for %s: %s", _BLOCK_PAUSE, message[:300])


def clear_block(db) -> None:
    row = _settings_row(db)
    if row and (row.youtube_blocked_until or row.youtube_last_error):
        row.youtube_blocked_until = None
        row.youtube_last_error = None
        db.commit()


def _raise_classified(db, exc: Exception) -> None:
    msg = str(exc)
    low = msg.lower()
    if any(h in low for h in _BLOCK_HINTS):
        _mark_blocked(db, msg)
        raise YouTubeBlockedError(msg) from exc
    if any(h in low for h in _UNAVAILABLE_HINTS):
        raise YouTubeUnavailableError(msg) from exc
    raise exc


# ── yt-dlp session ───────────────────────────────────────────────────────────

def _base_opts(db, workdir: Path) -> dict:
    """Quiet yt-dlp options + the optional Settings cookies/proxy. Cookies
    are decrypted into a 0600 file inside `workdir` (deleted with it)."""
    from app.services.encryption import decrypt

    opts: dict = {"quiet": True, "no_warnings": True, "noprogress": True, "socket_timeout": 30, "retries": 2}
    row = _settings_row(db)
    if row and row.youtube_proxy:
        opts["proxy"] = row.youtube_proxy.strip()
    if row and row.youtube_cookies_encrypted:
        cookie_path = workdir / "cookies.txt"
        fd = os.open(cookie_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(decrypt(row.youtube_cookies_encrypted))
        opts["cookiefile"] = str(cookie_path)
    return opts


def _pick_sub_track(info: dict) -> str | None:
    """The original-language track: the ASR `<lang>-orig` (it carries
    per-word timing), else a manual track in the video's language, else the
    plain ASR `<lang>`. Never a translated track."""
    auto = info.get("automatic_captions") or {}
    manual = {k: v for k, v in (info.get("subtitles") or {}).items() if k != "live_chat"}
    lang = (info.get("language") or "").strip()
    if not lang:
        orig = [k for k in auto if k.endswith("-orig")]
        lang = orig[0].removesuffix("-orig") if orig else ("en" if "en" in manual or "en" in auto else "")
    if not lang:
        return next(iter(manual), None)
    for key in (f"{lang}-orig",):
        if key in auto:
            return key
    for key in manual:
        if key == lang or key.startswith(f"{lang}-"):
            return key
    return lang if lang in auto else None


def _published(info: dict) -> datetime | None:
    ts = info.get("release_timestamp") or info.get("timestamp")
    if ts:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    day = info.get("upload_date")
    try:
        return datetime.strptime(day, "%Y%m%d") if day else None
    except ValueError:
        return None


def fetch_video(db, video_id: str, workdir: Path) -> tuple[VideoMeta, bytes | None]:
    """Metadata + the original subtitle track as json3 bytes (None when the
    video has no usable subtitles). Raises YouTubeBlockedError /
    YouTubeUnavailableError, or the underlying error for anything else."""
    import yt_dlp

    ensure_not_blocked(db)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        with yt_dlp.YoutubeDL({**_base_opts(db, workdir), "skip_download": True}) as ydl:
            info = ydl.extract_info(watch_url(video_id), download=False)
            track = _pick_sub_track(info)
            data = None
            if track:
                tracks = (info.get("automatic_captions") or {}).get(track) or (info.get("subtitles") or {}).get(track) or []
                fmt = next((t for t in tracks if t.get("ext") == "json3"), None)
                if fmt and fmt.get("url"):
                    data = ydl.urlopen(fmt["url"]).read()
    except yt_dlp.utils.DownloadError as exc:
        _raise_classified(db, exc)
    meta = VideoMeta(
        video_id=video_id,
        title=info.get("title") or "",
        channel=info.get("channel") or info.get("uploader") or "",
        duration_s=int(info["duration"]) if info.get("duration") else None,
        live_status=info.get("live_status"),
        published_at=_published(info),
        language=(track or "").removesuffix("-orig") or info.get("language"),
        sub_track=track if data else None,
    )
    return meta, data


def _ffprobe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=60,
    )
    try:
        return float(out.stdout.strip())
    except ValueError:
        return 0.0


def _run_download(db, video_id: str, workdir: Path, name: str, ranges: list[tuple[float, float]] | None) -> Path:
    import yt_dlp

    opts = {
        **_base_opts(db, workdir),
        "format": _SECTION_FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": str(workdir / f"{name}.%(ext)s"),
        "overwrites": True,
    }
    if ranges:
        opts["download_ranges"] = yt_dlp.utils.download_range_func(None, ranges)
        opts["force_keyframes_at_cuts"] = False   # our render re-encodes (and trims) anyway
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([watch_url(video_id)])
    except yt_dlp.utils.DownloadError as exc:
        _raise_classified(db, exc)
    produced = sorted(workdir.glob(f"{name}.*"), key=lambda p: p.stat().st_size, reverse=True)
    produced = [p for p in produced if p.suffix in (".mp4", ".mkv", ".webm")]
    if not produced:
        raise RuntimeError(f"yt-dlp produced no media file for {video_id}")
    return produced[0]


def download_clip_source(db, video_id: str, start: float, end: float, video_s: float | None, workdir: Path) -> Section:
    """The video between `start` and `end` (padded a little each side), as
    a local file plus the offset it starts at. Falls back to the whole
    video for short ones if a section download fails or comes back short."""
    ensure_not_blocked(db)
    workdir.mkdir(parents=True, exist_ok=True)
    sec_start = max(0.0, start - _SECTION_PAD_BEFORE)
    sec_end = end + _SECTION_PAD_AFTER if not video_s else min(video_s, end + _SECTION_PAD_AFTER)
    try:
        path = _run_download(db, video_id, workdir, "section", [(sec_start, sec_end)])
        got = _ffprobe_duration(path)
        if got >= (end - start) * 0.9:
            return Section(path=path, offset_s=sec_start)
        logger.warning("yt_downloader: section of %s came back %.1fs for %.1fs requested", video_id, got, sec_end - sec_start)
    except (YouTubeBlockedError, YouTubeUnavailableError):
        raise
    except Exception as exc:
        logger.warning("yt_downloader: section download failed for %s: %s", video_id, exc)
    if not video_s or video_s > _FULL_DOWNLOAD_MAX_S:
        raise RuntimeError(f"section download failed for {video_id} and the video is too long for a full download")
    path = _run_download(db, video_id, workdir, "full", None)
    return Section(path=path, offset_s=0.0)


_PROBE_VIDEO_ID = "jNQXAC9IVRw"   # "Me at the zoo" — YouTube's first upload, 19s, always public
_COOKIE_AUTH_NAMES = ("SID", "__Secure-1PSID", "__Secure-3PSID", "SAPISID", "LOGIN_INFO")


def validate_cookies_txt(text: str) -> str | None:
    """None if `text` looks like a usable Netscape cookies.txt for a signed-in
    YouTube/Google session, else a user-facing reason."""
    rows = [ln.split("\t") for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    rows = [r for r in rows if len(r) >= 7]
    if not rows:
        return "Not a Netscape cookies.txt file (export it with a 'Get cookies.txt' browser extension)."
    domains = {r[0].lstrip(".").lower() for r in rows}
    if not any(d.endswith("youtube.com") for d in domains):
        return "No youtube.com cookies in that file — export it while on youtube.com."
    names = {r[5] for r in rows}
    if not any(n in names for n in _COOKIE_AUTH_NAMES):
        return "No sign-in cookies (SID/SAPISID/LOGIN_INFO) — sign in to the burner account first."
    return None


def probe_access(db) -> dict:
    """One metadata request with the current cookies/proxy. Clears the
    circuit breaker on success."""
    import time
    import tempfile

    import yt_dlp

    started = time.monotonic()
    with tempfile.TemporaryDirectory() as tmp:
        try:
            with yt_dlp.YoutubeDL({**_base_opts(db, Path(tmp)), "skip_download": True}) as ydl:
                info = ydl.extract_info(watch_url(_PROBE_VIDEO_ID), download=False)
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:500], "ms": int((time.monotonic() - started) * 1000)}
    clear_block(db)
    return {"ok": True, "title": info.get("title"), "ms": int((time.monotonic() - started) * 1000)}


def cleanup_workdir(workdir: Path) -> None:
    shutil.rmtree(workdir, ignore_errors=True)
