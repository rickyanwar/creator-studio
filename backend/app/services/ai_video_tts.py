"""Add a clip-aligned narrator to a finished Mode 8 video.

Generate the whole script through Gemini TTS, split at pauses, or fall back per clip.
Derive clip windows from Flow's intermediate 100 ms tail trims.
Measure WAV speech and speed it up slightly or request shorter text.
Place speech after each clip's lead-in, preserving the required tail gap.
Render a full-length stereo narration track without changing clip timing.
Smoothly duck normalized Flow audio at known sentence times; normalize ASMR alone.
Copy the original video stream while encoding the mixed audio as AAC.
"""

import io
import json
import logging
import math
from pathlib import Path
import re
import struct
import subprocess
import tempfile
import time
import wave

import httpx

from app.services.nine_router import get_nine_router_config

TTS_MODELS = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts", "gemini-2.5-pro-preview-tts")
LEAD_IN_S = 0.25
TAIL_GAP_S = 0.5
MAX_TEMPO = 1.12
MAX_SHORTEN = 2
BED_LUFS = -16
VOICE_LUFS = -16
FINAL_LUFS = -14
DUCK_GAIN = 0.25
DUCK_DOWN_S = 0.2
DUCK_UP_S = 0.4
DUCK_HOLD_S = 0.15

logger = logging.getLogger(__name__)


class TtsError(Exception):
    """Narration synthesis, timing, or media processing failed."""


def _request_model(http, cfg, model, payload):
    error = "no response"
    for delay in (2, 6):
        try:
            response = http.post(
                f"{cfg.base_url.rstrip('/')}/audio/speech",
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                json={**payload, "model": model},
            )
            if response.is_success and response.content.startswith(b"RIFF"):
                return response.content, ""
            error = f"HTTP {response.status_code}" if not response.is_success else "non-RIFF response"
            if response.status_code != 429 and response.status_code < 500:
                break
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            error = type(exc).__name__
        time.sleep(delay)
    return None, error


def synthesize(text: str, voice: str, style: str, language: str, *, cfg=None, http=None) -> tuple[bytes, str]:
    """Synthesize a WAV using ordered models and two bounded attempts per model."""
    if not all(value.strip() for value in (text, voice, style, language)):
        raise TtsError("text, voice, style and language must not be empty")
    cfg = cfg if cfg is not None else get_nine_router_config()
    payload = {"input": f"{style.strip().rstrip(':.')} in {language}: {text.strip()}"}
    owned = http is None
    client = http if http is not None else httpx.Client(timeout=120)
    errors = []
    try:
        for model in TTS_MODELS:
            wav, error = _request_model(client, cfg, f"gemini/{model}/{voice}", payload)
            if wav is not None:
                return wav, model
            errors.append(f"{model}: {error}")
    finally:
        if owned:
            client.close()
    raise TtsError("TTS failed: " + "; ".join(errors))


def _run(args, timeout=300):
    try:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=timeout)
    except subprocess.CalledProcessError as exc:
        raise TtsError(f"{args[0]} failed: {(exc.stderr or '')[-2000:]}") from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TtsError(f"{args[0]} failed: {type(exc).__name__}") from exc


def _probe(path):
    result = _run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], 30)
    try:
        info = json.loads(result.stdout)
        duration = float(info["format"]["duration"])
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("invalid duration")
        return info, duration
    except (ValueError, KeyError, TypeError) as exc:
        raise TtsError("ffprobe returned invalid media information") from exc


def _audio_duration(path) -> float:
    info, duration = _probe(path)
    stream = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    # WAV stores duration in the container; video containers must report audio duration.
    fallback = duration if info["format"].get("format_name") == "wav" and stream else 0
    try:
        value = float(stream.get("duration", fallback))
        return value if math.isfinite(value) and value >= 0 else 0
    except (ValueError, TypeError):
        return 0


def _render_checked_audio(args, out_path, duration):
    for _ in range(2):
        _run(args)
        if _audio_duration(out_path) >= duration - 0.15:
            return
    raise TtsError("mixed audio too short")


def clip_windows(video_path: str, clips: int, clip_seconds: float = 10.0) -> list[tuple[float, float]]:
    """Account for the intermediate 100 ms trims and the actual final duration."""
    if clips < 1 or not math.isfinite(clip_seconds) or clip_seconds <= 0.1:
        raise TtsError("clips must be positive and clip_seconds must exceed 0.1")
    _, duration = _probe(video_path)
    step = clip_seconds - 0.1
    if duration <= (clips - 1) * step:
        raise TtsError("video is too short for the clip windows")
    return [(i * step, (i + 1) * step if i < clips - 1 else duration) for i in range(clips)]


def _wav_duration(data):
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            duration = wav.getnframes() / wav.getframerate()
        if duration <= 0:
            raise ValueError("empty WAV")
        return duration
    except (wave.Error, EOFError, ValueError, ZeroDivisionError) as exc:
        raise TtsError("invalid TTS WAV") from exc


def split_by_pauses(wav_bytes, word_counts) -> list[bytes] | None:
    """Split on the longest internal pauses, rejecting implausible sentence shares."""
    if not word_counts or any(count <= 0 for count in word_counts):
        return None
    with tempfile.TemporaryDirectory() as temporary:
        source, pcm = Path(temporary) / "source.wav", Path(temporary) / "pcm.wav"
        source.write_bytes(wav_bytes)
        result = _run(["ffmpeg", "-y", "-i", str(source), "-af",
                       "silencedetect=noise=-35dB:d=0.25", "-ac", "1", "-ar", "24000",
                       "-c:a", "pcm_s16le", str(pcm)])
        with wave.open(str(pcm), "rb") as wav:
            rate, frames = wav.getframerate(), wav.readframes(wav.getnframes())
    duration = len(frames) / 2 / rate
    pauses, start = [], None
    for kind, value in re.findall(r"silence_(start|end):\s*([\d.]+)", result.stderr):
        if kind == "start":
            start = float(value)
        elif start is not None:
            end = float(value)
            if start > 0 and end < duration - 1 / rate:
                pauses.append((start, end))
            start = None
    needed = len(word_counts) - 1
    if len(pauses) < needed:
        return None
    chosen = sorted(sorted(pauses, key=lambda p: p[1] - p[0], reverse=True)[:needed])
    cuts = [0, *[round((a + b) / 2 * rate) for a, b in chosen], len(frames) // 2]
    parts, lengths = [], []
    for left, right in zip(cuts, cuts[1:]):
        first, last = None, None
        for i, (sample,) in enumerate(struct.iter_unpack("<h", frames[left * 2:right * 2])):
            if abs(sample) > 32768 * 10 ** (-35 / 20):
                if first is None:
                    first = i
                last = i
        if first is None:
            return None
        left, right = left + first, left + last + 1
        lengths.append((right - left) / rate)
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            wav.writeframes(frames[left * 2:right * 2])
        parts.append(buffer.getvalue())
    total_words, total_length = sum(word_counts), sum(lengths)
    if any(length < 0.8 or not 0.55 <= (length / total_length) / (words / total_words) <= 1.45
           for length, words in zip(lengths, word_counts)):
        return None
    return parts


def _fit_segment(text, voice, style, language, max_len, index, synth, shorten, initial=None):
    if not isinstance(text, str) or not text.strip():
        raise TtsError(f"narration {index} is empty")
    if max_len <= 0:
        raise TtsError(f"narration {index} too long for clip window")
    text = text.strip()
    for round_index in range(MAX_SHORTEN + 1):
        data, model = initial if round_index == 0 and initial is not None else synth(text, voice, style, language)
        duration = _wav_duration(data)
        tempo = max(1.0, duration / max_len)
        if tempo <= MAX_TEMPO:
            return data, model, text, duration / tempo, tempo
        if shorten is None or round_index == MAX_SHORTEN:
            break
        max_words = max(3, int(len(text.split()) * max_len / duration))
        text = shorten(text, max_words)
        if not isinstance(text, str) or not text.strip():
            raise TtsError(f"narration {index} is empty")
        text = text.strip()
    raise TtsError(f"narration {index} too long for clip window")


def _render_track(paths, segments, duration, audio_path):
    rate, channels, sample_width = 48000, 2, 2
    total_frames = round(duration * rate)
    track = bytearray(total_frames * channels * sample_width)
    with tempfile.TemporaryDirectory() as temporary:
        for i, (path, segment) in enumerate(zip(paths, segments)):
            raw = Path(temporary) / f"{i}.raw"
            _run(["ffmpeg", "-y", "-v", "error", "-i", str(path), "-af",
                  f"atempo={segment['tempo']:.12f}", "-t", f"{segment['duration']:.3f}",
                  "-f", "s16le", "-ac", str(channels), "-ar", str(rate), str(raw)])
            data = raw.read_bytes()
            start = round(segment["start"] * rate) * channels * sample_width
            if start >= len(track):
                raise TtsError("segment starts after the video")
            data = data[:len(track) - start]
            # Clip windows do not overlap; later segments overwrite if they ever do.
            track[start:start + len(data)] = data
    with wave.open(str(audio_path), "wb") as wav:
        wav.setparams((channels, sample_width, rate, 0, "NONE", "not compressed"))
        wav.writeframes(track)


def build_narration(video_path: str, narrations: list[str], voice: str, style: str, language: str, out_dir: str,
                    *, shorten=None, synth=synthesize, clip_seconds: float = 10.0) -> dict:
    """Synthesize once, split and fit sentences, falling back to per-clip requests."""
    windows = clip_windows(video_path, len(narrations), clip_seconds)
    if any(not isinstance(text, str) or not text.strip() for text in narrations):
        raise TtsError("narration is empty")
    sentences = [text.strip() for text in narrations]
    requests = 0
    def counted_synth(*args):
        nonlocal requests
        requests += 1
        return synth(*args)
    full_wav, single_model = counted_synth("\n\n".join(sentences), voice, style, language)
    parts = split_by_pauses(full_wav, [len(text.split()) for text in sentences])
    mode = "single" if parts is not None else "per_clip"
    if parts is None:
        logger.warning("single-request TTS split failed, per-clip fallback")
    output = Path(out_dir)
    output.mkdir(parents=True, exist_ok=True)
    audio_path = output / "narration.wav"
    segments, paths = [], []
    with tempfile.TemporaryDirectory(dir=output) as temporary:
        for index, (text, (start, end)) in enumerate(zip(sentences, windows), 1):
            data, model, text, duration, tempo = _fit_segment(
                text, voice, style, language, end - start - LEAD_IN_S - TAIL_GAP_S,
                index, counted_synth, shorten, (parts[index - 1], single_model) if parts is not None else None,
            )
            path = Path(temporary) / f"{index}.wav"
            path.write_bytes(data)
            paths.append(path)
            segments.append({"index": index, "text": text, "start": start + LEAD_IN_S,
                             "duration": duration, "tempo": tempo})
        _render_track(paths, segments, windows[-1][1], audio_path)
    if _audio_duration(audio_path) < windows[-1][1] - 0.05:
        raise TtsError("narration track too short")
    for segment in segments:
        segment.update(start=round(segment["start"], 3), duration=round(segment["duration"], 3))
    return {"audio_path": str(audio_path), "model": model, "segments": segments,
            "requests": requests, "mode": mode}


def duck_expression(segments) -> str:
    """Return a filtergraph-safe envelope taking the lowest overlapping gain."""
    expression = "1"
    for segment in segments:
        start, duration = float(segment["start"]), float(segment["duration"])
        if not math.isfinite(start) or not math.isfinite(duration) or start < 0 or duration <= 0:
            raise TtsError("invalid ducking segment")
        end = start + duration + DUCK_HOLD_S
        envelope = (f"1-{1 - DUCK_GAIN}*min(clip((t-({start - DUCK_DOWN_S}))/{DUCK_DOWN_S},0,1),"
                    f"clip(({end + DUCK_UP_S}-t)/{DUCK_UP_S},0,1))")
        expression = f"min({expression},{envelope})"
    return expression.replace(",", "\\,")


def normalize_bed(video_path: str, out_path: str) -> str:
    """Normalize narrator-free Flow audio while copying the original video."""
    info, duration = _probe(video_path)
    if not any(stream["codec_type"] == "audio" for stream in info["streams"]):
        raise TtsError("video has no audio")
    _render_checked_audio(["ffmpeg", "-y", "-v", "error", "-i", str(video_path), "-map", "0:v:0", "-map", "0:a:0",
          "-af", f"loudnorm=I={FINAL_LUFS}:TP=-1.5:LRA=11,alimiter=limit=0.95,apad=whole_dur={duration:.12f},atrim=duration={duration:.12f}",
          "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}",
          "-movflags", "+faststart", str(out_path)], out_path, duration)
    return out_path


def _linear_mix_normalization(video_path, narration_wav, filters):
    """Measure once so final normalization preserves the intentional gain envelope."""
    measured = _run([
        "ffmpeg", "-i", str(video_path), "-i", str(narration_wav), "-filter_complex",
        filters + f"loudnorm=I={FINAL_LUFS}:TP=-1.5:LRA=11:print_format=json[out]",
        "-map", "[out]", "-t", f"{_probe(video_path)[1]:.3f}", "-f", "null", "-",
    ])
    stats, _ = json.JSONDecoder().raw_decode(measured.stderr[measured.stderr.rfind("{"):])
    if not all(math.isfinite(float(stats[key])) for key in
               ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")):
        return f"loudnorm=I={FINAL_LUFS}:TP=-1.5:LRA=11"
    return (f"loudnorm=I={FINAL_LUFS}:TP=-1.5:LRA=11:linear=true:"
            f"measured_I={stats['input_i']}:measured_TP={stats['input_tp']}:"
            f"measured_LRA={stats['input_lra']}:measured_thresh={stats['input_thresh']}:"
            f"offset={stats['target_offset']}")


def mix_narration(video_path: str, narration_wav: str, out_path: str, segments=None) -> str:
    """Duck Flow audio under speech and preserve the complete copied video."""
    info, duration = _probe(video_path)
    filters = f"[1:a]apad=whole_dur={duration:.12f},atrim=duration={duration:.12f},asetpts=PTS-STARTPTS[voice];"
    if any(stream["codec_type"] == "audio" for stream in info["streams"]) and segments is not None:
        filters += (
            f"[0:a:0]loudnorm=I={BED_LUFS}:TP=-2:LRA=11,"
            # Bound frame size after loudnorm so volume follows sentence ramps at 20 ms resolution.
            f"aresample=48000,asetnsamples=n=960:p=0,volume='{duck_expression(segments)}':eval=frame[bed];"
            f"[voice]loudnorm=I={VOICE_LUFS}:TP=-2:LRA=11[speech];"
            "[bed][speech]amix=inputs=2:normalize=0[mixed];[mixed]"
        )
    elif any(stream["codec_type"] == "audio" for stream in info["streams"]):
        filters += (
            "[voice]asplit=2[key][speech];"
            "[0:a:0][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=400[ducked];"
            "[ducked][speech]amix=inputs=2:normalize=0[mixed];"
            "[mixed]"
        )
    else:
        filters += "[voice]"
    normalization = (_linear_mix_normalization(video_path, narration_wav, filters) if segments is not None
                     else f"loudnorm=I={FINAL_LUFS}:TP=-1.5:LRA=11")
    filters += (f"{normalization},alimiter=limit=0.95,"
                f"apad=whole_dur={duration:.12f},atrim=duration={duration:.12f}[out]")
    _render_checked_audio(["ffmpeg", "-y", "-v", "error", "-i", str(video_path), "-i", str(narration_wav),
          "-filter_complex", filters, "-map", "0:v:0", "-map", "[out]", "-c:v", "copy",
          "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-t", f"{duration:.3f}", "-movflags", "+faststart", str(out_path)],
                          out_path, duration)
    return out_path


def shorten_narration(text: str, max_words: int, language: str) -> str:
    """Rewrite one sentence within a strict word budget using text-model fallback."""
    from app.services.news_copywriter import _generate_with_fallback

    prompt = (
        f"Rewrite the sentence in {language} in at most {max_words} words. "
        "Keep the same meaning and facts. Do not add any fact that is not in the sentence. "
        "Return only one sentence, without quotes or commentary.\nSentence: " + text
    )

    def parse(reply):
        sentence = reply.strip()
        if not sentence or any(quote in sentence for quote in ('"', '“', '”')) or len(sentence.split()) > max_words:
            raise ValueError("Narration must be nonempty, unquoted, and within the word limit")
        return sentence

    sentence, _ = _generate_with_fallback(prompt, parse, context="ai_video_tts_shorten")
    return sentence


def narrate_video(job, texts: list[str], out_dir: str) -> dict:
    """Mix a fitted narrator into the raw video and retain its clean caption audio."""
    import shutil
    import uuid

    narration = job.narration
    result = build_narration(
        job.video_path, texts, narration["voice"], narration["style"], narration["language"], out_dir,
        shorten=lambda text, words: shorten_narration(text, words, narration["language"]),
        clip_seconds=job.clip_seconds or 10.0,
    )
    raw = Path(job.video_path)
    mixed = raw.with_name(f"{raw.stem}_tts_{uuid.uuid4().hex[:8]}.mp4")
    mix_narration(str(raw), result["audio_path"], str(mixed), segments=result["segments"])
    kept_wav = raw.parent / f"narration_job_{job.id}_{uuid.uuid4().hex[:8]}.wav"
    shutil.move(result["audio_path"], kept_wav)
    return {"video_path": str(mixed), "audio_path": str(kept_wav),
            "model": result["model"], "segments": result["segments"]}
