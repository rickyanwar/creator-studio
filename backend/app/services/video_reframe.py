"""16:9 → 9:16 reframe + single-pass encode for Mode 7 clips.

Per shot (not per video): race footage has no faces, and a centre crop of a
car on track shows mostly tarmac, so a shot only gets a face-following crop
when a face genuinely dominates it:

  Pass 1 — analysis, cheap: decode the clip at 5 fps / 640 px, cut it into
  shots (HSV-histogram jumps), run YuNet on every sample. A face counts only
  if it's big (≥ 8 % of the frame height), confident, and still there in a
  neighbouring sample (single-sample detections are the false positives
  YuNet makes on dark textured areas).
    TRACK — a persistent face in ≥ 60 % of a shot's samples: a 9:16 crop
            follows the dominant face (sticky to the one being followed),
            smoothed (dead-zone + EMA), jumping straight to the new subject
            on a cut.
    FIT   — no dominant face, or two big faces too far apart for one crop:
            the whole frame, centred on a blurred, darkened copy of itself.
            Nothing is lost.
  Shots under 1 s inherit the previous shot's mode (no flicker).

  Pass 2 — render: decode at 30 fps full-res (ffmpeg decodes, so variable
  frame-rate sources keep audio sync), compose each frame, and pipe it into
  ONE ffmpeg encode that also burns the captions and the watermark.

Verified on real clips 2026-09-25 (interview → TRACK following speaker and
host across cuts; press-conference sofa wide shot → FIT; F1 onboard/wide
race footage → all FIT). VPS, 2 CPUs: ~55-85 s per 60 s clip, ~1.2 GB peak.
"""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

OUT_W, OUT_H = 1080, 1920
FPS = 30
_SAMPLE_FPS = 5
_ANALYSIS_W = 640
_FACE_MIN_SCORE = 0.75
_FACE_MIN_H = 0.08
_BIG_FACE_H = 0.12
_CUT_THRESHOLD = 0.45
_TRACK_MIN_FRACTION = 0.6
_DEAD_ZONE = 0.03
_EMA_ALPHA = 0.35
_WATERMARK_W = 130           # ~12 % of the width
_WATERMARK_MARGIN = 42
# Memory (measured on the VPS, 2026-09-25 — ~1.4 GB free there): the encoder
# first peaked at ~1.25 GB and the host OOM-killed it. Cause: -shortest (see
# _encoder_cmd). Also kept lean: frames piped as yuv420p (3 MB vs 6 MB bgr24),
# short input queues, 2 threads for x264 and the decoders (the worker is
# capped at 2 CPUs anyway).
_THREADS = "2"
_X264 = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-threads", _THREADS]


@dataclass(frozen=True)
class ClipSpec:
    src: Path
    seek_s: float            # where the clip starts inside `src`
    duration_s: float
    out: Path
    ass_path: Path | None = None
    fonts_dir: Path | None = None
    watermark_png: Path | None = None
    watermark_text: str | None = None


@dataclass(frozen=True)
class RenderResult:
    duration_s: float
    thumbnail: Path
    shots: list[tuple[float, float, str]]


def _probe_size(path: Path) -> tuple[int, int]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _decode(spec: ClipSpec, w: int, h: int, vf: str):
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-threads", _THREADS, "-ss", f"{spec.seek_s:.3f}", "-t", f"{spec.duration_s:.3f}",
         "-i", str(spec.src), "-vf", vf, "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE,
        # a decode we stop reading early (encoder failed) only ever reports
        # "Broken pipe" here; real decode failures surface as missing frames
        # (render_clip's sanity check)
        stderr=subprocess.DEVNULL,
    )
    size = w * h * 3
    try:
        while len(buf := proc.stdout.read(size)) == size:
            yield np.frombuffer(buf, np.uint8).reshape(h, w, 3)
    finally:
        proc.stdout.close()
        proc.wait()


# ── pass 1: analysis ─────────────────────────────────────────────────────────

def _analyse(spec: ClipSpec, src_w: int, src_h: int) -> tuple[list[np.ndarray], list[list[tuple]]]:
    from app.services.design_images import _get_yunet_detector

    detector = _get_yunet_detector()
    aw, ah = _ANALYSIS_W, int(round(src_h * _ANALYSIS_W / src_w / 2) * 2)
    if detector is not None:
        detector.setInputSize((aw, ah))
    hists, faces = [], []
    for frame in _decode(spec, aw, ah, f"fps={_SAMPLE_FPS},scale={aw}:{ah}"):
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        hists.append(cv2.normalize(hist, hist).flatten())
        found = []
        if detector is not None:
            _, det = detector.detect(frame)
            for f in det if det is not None else []:
                x, y, fw, fh, score = float(f[0]), float(f[1]), float(f[2]), float(f[3]), float(f[-1])
                if score >= _FACE_MIN_SCORE and fh / ah >= _FACE_MIN_H:
                    found.append(((x + fw / 2) / aw, (y + fh / 2) / ah, fw / aw, fh / ah))
        faces.append(found)
    return hists, faces


def _shots(hists: list[np.ndarray]) -> list[tuple[int, int]]:
    cuts = [0] + [
        i for i in range(1, len(hists))
        if cv2.compareHist(hists[i - 1], hists[i], cv2.HISTCMP_BHATTACHARYYA) > _CUT_THRESHOLD
    ] + [len(hists)]
    return [(a, b) for a, b in zip(cuts, cuts[1:]) if b > a]


def _persistent(faces: list[list[tuple]], i: int) -> list[tuple]:
    return [
        f for f in faces[i]
        if any(
            0 <= j < len(faces) and any(abs(f[0] - g[0]) < 0.08 and abs(f[1] - g[1]) < 0.1 for g in faces[j])
            for j in (i - 1, i + 1)
        )
    ]


def _shot_modes(shots: list[tuple[int, int]], faces: list[list[tuple]], crop_frac: float) -> list[str]:
    modes = []
    for a, b in shots:
        with_face = two_far = 0
        for i in range(a, b):
            pf = _persistent(faces, i)
            if not pf:
                continue
            with_face += 1
            big = sorted((f for f in pf if f[3] >= _BIG_FACE_H), key=lambda f: -f[3])[:2]
            if len(big) == 2 and abs(big[0][0] - big[1][0]) > crop_frac * 0.8:
                two_far += 1
        track = with_face / (b - a) >= _TRACK_MIN_FRACTION and two_far / max(1, with_face) < 0.5
        modes.append("TRACK" if track else "FIT")
    for k, (a, b) in enumerate(shots):
        if k and b - a < _SAMPLE_FPS:
            modes[k] = modes[k - 1]
    return modes


def _track_centres(shots, modes, faces) -> list[float]:
    centre = [0.5] * len(faces)
    for (a, b), mode in zip(shots, modes):
        if mode != "TRACK":
            continue
        cur, last_target = None, 0.5
        for i in range(a, b):
            pf = _persistent(faces, i) or faces[i]
            if pf:
                last_target = max(pf, key=lambda f: f[3] - (abs(f[0] - cur) * 0.5 if cur is not None else 0))[0]
            if cur is None:
                cur = last_target
            elif abs(last_target - cur) > _DEAD_ZONE:
                cur += (last_target - cur) * _EMA_ALPHA
            centre[i] = cur
    return centre


# ── pass 2: compose + encode ─────────────────────────────────────────────────

def _compose_fit(frame: np.ndarray) -> np.ndarray:
    h, w = frame.shape[:2]
    fg_h = int(round(OUT_W * h / w / 2) * 2)
    fg = cv2.resize(frame, (OUT_W, fg_h), interpolation=cv2.INTER_AREA)
    crop_w = int(h * OUT_W / OUT_H)
    x0 = (w - crop_w) // 2
    bg = cv2.resize(frame[:, x0:x0 + crop_w], (OUT_W // 8, OUT_H // 8), interpolation=cv2.INTER_AREA)
    bg = cv2.GaussianBlur(bg, (0, 0), 6)
    bg = cv2.resize((bg * 0.55).astype(np.uint8), (OUT_W, OUT_H), interpolation=cv2.INTER_LINEAR)
    y0 = (OUT_H - fg_h) // 2
    bg[y0:y0 + fg_h] = fg
    return bg


def _compose_track(frame: np.ndarray, centre: float) -> np.ndarray:
    h, w = frame.shape[:2]
    crop_w = int(h * OUT_W / OUT_H)
    x0 = max(0, min(w - crop_w, int(round(centre * w - crop_w / 2))))
    return cv2.resize(frame[:, x0:x0 + crop_w], (OUT_W, OUT_H), interpolation=cv2.INTER_CUBIC)


def _filter_path(p: Path) -> str:
    return "'" + str(p).replace("\\", "/").replace("'", "") + "'"


def _encoder_cmd(spec: ClipSpec) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-y", "-filter_threads", "1",
           "-thread_queue_size", "4", "-f", "rawvideo", "-pix_fmt", "yuv420p", "-s", f"{OUT_W}x{OUT_H}",
           "-r", str(FPS), "-i", "-",
           "-thread_queue_size", "64", "-ss", f"{spec.seek_s:.3f}", "-t", f"{spec.duration_s:.3f}", "-i", str(spec.src)]
    chain, label = [], "0:v"
    if spec.ass_path:
        fonts = f":fontsdir={_filter_path(spec.fonts_dir)}" if spec.fonts_dir else ""
        chain.append(f"[{label}]ass=filename={_filter_path(spec.ass_path)}{fonts}[vc]")
        label = "vc"
    if spec.watermark_png:
        cmd += ["-i", str(spec.watermark_png)]
        chain.append(f"[2:v]scale={_WATERMARK_W}:-1,format=rgba,colorchannelmixer=aa=0.9[wm]")
        chain.append(f"[{label}][wm]overlay=W-w-{_WATERMARK_MARGIN}:{_WATERMARK_MARGIN + 40}[vw]")
        label = "vw"
    elif spec.watermark_text and spec.fonts_dir:
        text = spec.watermark_text.replace("\\", "").replace("'", "").replace(":", "\\:")[:40]
        font = spec.fonts_dir / "Poppins-Black.ttf"
        chain.append(
            f"[{label}]drawtext=fontfile={_filter_path(font)}:text='{text}':fontsize=34:fontcolor=white@0.85"
            f":shadowcolor=black@0.6:shadowx=2:shadowy=2:x=w-tw-{_WATERMARK_MARGIN}:y={_WATERMARK_MARGIN + 40}[vw]"
        )
        label = "vw"
    if chain:
        cmd += ["-filter_complex", ";".join(chain), "-map", f"[{label}]"]
    else:
        cmd += ["-map", "0:v"]
    # Bounded with -t, NOT -shortest: in ffmpeg 7.1, -shortest with the seeked
    # audio input made the encoder buffer ~900 MB extra (measured: 1.25 GB vs
    # 379 MB for the same encode with -t).
    return cmd + ["-map", "1:a?", *_X264, "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
                  "-t", f"{spec.duration_s:.3f}", "-movflags", "+faststart", str(spec.out)]


def _render_frames(spec: ClipSpec, src_w: int, src_h: int, sample_modes: list[str], centres: list[float]) -> None:
    enc = subprocess.Popen(_encoder_cmd(spec), stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    last = len(sample_modes) - 1
    try:
        for n, frame in enumerate(_decode(spec, src_w, src_h, f"fps={FPS}")):
            pos = n * _SAMPLE_FPS / FPS
            s = min(last, int(pos))
            if sample_modes[s] == "TRACK":
                nxt = min(last, s + 1)
                c = centres[s] + (centres[nxt] - centres[s]) * (pos - s) if sample_modes[nxt] == "TRACK" else centres[s]
                out = _compose_track(frame, c)
            else:
                out = _compose_fit(frame)
            enc.stdin.write(cv2.cvtColor(out, cv2.COLOR_BGR2YUV_I420).tobytes())
    except BrokenPipeError:
        pass
    finally:
        enc.stdin.close()
        err = enc.stderr.read().decode(errors="replace")
        enc.wait()
    if enc.returncode != 0:
        raise RuntimeError(f"ffmpeg encode failed ({enc.returncode}): {err[-800:]}")


def _probe_output(path: Path) -> tuple[float, bool, tuple[int, int]]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height", "-of", "json", str(path)],
        capture_output=True, text=True, check=True, timeout=60,
    ).stdout
    d = json.loads(out)
    video = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    has_audio = any(s.get("codec_type") == "audio" for s in d.get("streams", []))
    return float(d.get("format", {}).get("duration") or 0), has_audio, (video.get("width", 0), video.get("height", 0))


def render_clip(spec: ClipSpec) -> RenderResult:
    """Analyse, reframe and encode one clip. Raises RuntimeError if the
    output fails its sanity check (duration, audio, resolution)."""
    src_w, src_h = _probe_size(spec.src)
    hists, faces = _analyse(spec, src_w, src_h)
    if not hists:
        raise RuntimeError("no frames decoded from the source clip")
    shots = _shots(hists)
    modes = _shot_modes(shots, faces, (src_h * OUT_W / OUT_H) / src_w)
    centres = _track_centres(shots, modes, faces)
    sample_modes = [""] * len(hists)
    for (a, b), m in zip(shots, modes):
        sample_modes[a:b] = [m] * (b - a)
    _render_frames(spec, src_w, src_h, sample_modes, centres)

    duration, has_audio, size = _probe_output(spec.out)
    if abs(duration - spec.duration_s) > 1.5 or size != (OUT_W, OUT_H) or not has_audio:
        raise RuntimeError(
            f"clip failed its sanity check: {duration:.1f}s (want {spec.duration_s:.1f}s), "
            f"{size[0]}x{size[1]}, audio={has_audio}"
        )
    thumb = spec.out.with_suffix(".jpg")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-ss", f"{min(duration / 3, 8):.2f}", "-i", str(spec.out),
         "-frames:v", "1", "-q:v", "3", str(thumb)],
        check=True, timeout=60,
    )
    shot_list = [(a / _SAMPLE_FPS, b / _SAMPLE_FPS, m) for (a, b), m in zip(shots, modes)]
    logger.info("video_reframe: %s — %d shots (%s), %.1fs", spec.out.name, len(shots),
                ", ".join(f"{m}" for *_, m in shot_list[:12]), duration)
    return RenderResult(duration_s=duration, thumbnail=thumb, shots=shot_list)
