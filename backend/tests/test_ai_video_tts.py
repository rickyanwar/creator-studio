import io
import json
import math
from pathlib import Path
import struct
import subprocess
from types import SimpleNamespace
import wave

import httpx
import pytest

from app.services import ai_video_tts as tts


def wav_bytes(duration):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / 24000)))
                                 for i in range(round(duration * 24000))))
    return buffer.getvalue()


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def video(tmp_path_factory):
    directory = tmp_path_factory.mktemp("tts-media")
    path = directory / "three-clips.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=180x320:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=300:sample_rate=48000", "-t", "29.8",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True, capture_output=True, timeout=300,
    )
    return path


@pytest.fixture
def cfg():
    return SimpleNamespace(base_url="https://router.invalid/v1", api_key="test-key")


def test_synthesize_fallback(monkeypatch, cfg):
    requests, sleeps = [], []
    data = wav_bytes(0.1)

    def handler(request):
        requests.append(json.loads(request.content))
        assert str(request.url) == "https://router.invalid/v1/audio/speech"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(502) if len(requests) <= 2 else httpx.Response(200, content=data)

    monkeypatch.setattr(tts.time, "sleep", sleeps.append)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        result, model = tts.synthesize(" Hello there. ", "Charon", " Calm documentary tone:. ", "English", cfg=cfg, http=http)
    assert result == data and model == tts.TTS_MODELS[1]
    assert sleeps == [2, 6]
    assert requests == [
        {"model": f"gemini/{model}/Charon", "input": "Calm documentary tone in English: Hello there."}
        for model in (tts.TTS_MODELS[0], tts.TTS_MODELS[0], tts.TTS_MODELS[1])
    ]


@pytest.mark.parametrize("status,body,calls", [(400, b"bad request", 3), (200, b"not WAV", 3), (502, b"error", 6)])
def test_synthesize_all_fail(monkeypatch, cfg, status, body, calls):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, content=body)

    monkeypatch.setattr(tts.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(tts.TtsError) as error:
            tts.synthesize("Hello", "Charon", "Calm", "English", cfg=cfg, http=http)
    assert len(requests) == calls
    assert all(model in str(error.value) for model in tts.TTS_MODELS)
    assert cfg.api_key not in str(error.value)


@pytest.mark.parametrize("failure", [429, httpx.ReadTimeout, httpx.ConnectError])
def test_synthesize_retry(monkeypatch, cfg, failure):
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            if isinstance(failure, int):
                return httpx.Response(failure)
            raise failure("test failure", request=request)
        return httpx.Response(200, content=wav_bytes(0.1))

    monkeypatch.setattr(tts.time, "sleep", lambda _: None)
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        assert tts.synthesize("Hello", "Charon", "Calm", "English", cfg=cfg, http=http)[1] == tts.TTS_MODELS[0]
    assert len(calls) == 2


def test_clip_windows(video):
    windows = tts.clip_windows(str(video), 3)
    for actual, expected in zip(windows, [(0, 9.9), (9.9, 19.8), (19.8, 29.8)]):
        assert actual == pytest.approx(expected, abs=0.05)
    with pytest.raises(tts.TtsError):
        tts.clip_windows(str(video), 0)
    with pytest.raises(tts.TtsError):
        tts.clip_windows(str(video.with_name("missing.mp4")), 3)


def test_build_fitting(video, tmp_path):
    result = tts.build_narration(str(video), ["One", "Two", "Three"], "Charon", "Calm", "English", str(tmp_path),
                                 synth=lambda *args: (wav_bytes(2), "fake-model"))
    assert result["model"] == "fake-model"
    assert [s["index"] for s in result["segments"]] == [1, 2, 3]
    assert [s["start"] for s in result["segments"]] == pytest.approx([0.25, 10.15, 20.05])
    assert all(s["tempo"] == 1.0 and s["duration"] == 2 for s in result["segments"])
    with wave.open(result["audio_path"], "rb") as wav:
        assert wav.getframerate() == 48000 and wav.getnchannels() == 2
        assert wav.getnframes() / wav.getframerate() == pytest.approx(float(probe(video)["format"]["duration"]), abs=0.05)
        wav.setpos(4800)
        assert not any(wav.readframes(2400))
        wav.setpos(14400)
        assert any(wav.readframes(2400))


@pytest.mark.parametrize("tempo", [1.0, 1.08])
def test_render_track_exact_frames_and_starts(tmp_path, tempo):
    source, output = tmp_path / "tone.wav", tmp_path / "track.wav"
    source.write_bytes(wav_bytes(0.4))
    duration = 2.123456
    segments = [{"start": start, "duration": 0.3, "tempo": tempo} for start in (0.25, 1.25)]
    tts._render_track([source, source], segments, duration, output)
    with wave.open(str(output), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (2, 2, 48000)
        assert abs(wav.getnframes() - duration * 48000) <= 1
        assert not any(wav.readframes(round(segments[0]["start"] * 48000)))
        for segment in segments:
            wav.setpos(round(segment["start"] * 48000))
            assert any(wav.readframes(480))
    with pytest.raises(tts.TtsError, match="segment starts after the video"):
        tts._render_track([source], [{**segments[0], "start": 3}], duration, output)


def test_build_rejects_short_track(video, tmp_path, monkeypatch):
    monkeypatch.setattr(tts, "_audio_duration", lambda path: 0.25)
    with pytest.raises(tts.TtsError, match="narration track too short"):
        tts.build_narration(str(video), ["One"], "Charon", "Calm", "English", str(tmp_path),
                            synth=lambda *args: (wav_bytes(1), "fake"))


@pytest.mark.parametrize("operation", [tts.mix_narration, tts.normalize_bed])
@pytest.mark.parametrize("durations", [(0.25, 29.8), (0.25, 0.25)])
def test_audio_output_retries_once(monkeypatch, operation, durations):
    calls, lengths = [], iter(durations)
    monkeypatch.setattr(tts, "_probe", lambda path: ({"streams": [{"codec_type": "audio"}]}, 29.8))
    monkeypatch.setattr(tts, "_run", lambda args: calls.append(args))
    monkeypatch.setattr(tts, "_audio_duration", lambda path: next(lengths))
    args = ("video.mp4", "speech.wav", "mixed.mp4") if operation == tts.mix_narration else ("video.mp4", "bed.mp4")
    if durations[-1] == 0.25:
        with pytest.raises(tts.TtsError, match="mixed audio too short"):
            operation(*args)
    else:
        assert operation(*args) == args[-1]
    assert len(calls) == 2
    assert calls[0] == calls[1] and calls[0][0] == "ffmpeg"


@pytest.mark.parametrize("format_name,stream,expected", [
    ("mov,mp4", {"codec_type": "audio", "duration": "0.25"}, 0.25),
    ("mov,mp4", {"codec_type": "audio"}, 0),
    ("wav", {"codec_type": "audio"}, 30),
    ("mov,mp4", {"codec_type": "audio", "duration": "N/A"}, 0),
])
def test_audio_duration_uses_audio_stream(monkeypatch, format_name, stream, expected):
    monkeypatch.setattr(tts, "_probe", lambda path: ({"format": {"format_name": format_name}, "streams": [stream]}, 30))
    assert tts._audio_duration("output") == expected


def test_build_tempo(video, tmp_path):
    result = tts.build_narration(str(video), ["One", "Two", "Three"], "Charon", "Calm", "English", str(tmp_path),
                                 synth=lambda *args: (wav_bytes(9.15 * 1.08), "fake"))
    assert result["segments"][0]["tempo"] == pytest.approx(1.08, abs=0.001)
    windows = tts.clip_windows(str(video), 3)
    for segment, (_, end) in zip(result["segments"], windows):
        assert segment["start"] + segment["duration"] <= end - tts.TAIL_GAP_S + 0.001


def test_build_shorten(video, tmp_path):
    text = "one two three four five six seven eight nine ten"
    shortened = "one two three four five six"
    calls = []

    def synth(text, *args):
        return wav_bytes(len(text.split()) * 9.15 * 1.4 / 10), "fake"

    def shorten(text, max_words):
        calls.append((text, max_words))
        return shortened

    result = tts.build_narration(str(video), [text] * 3, "Charon", "Calm", "English", str(tmp_path),
                                 synth=synth, shorten=shorten)
    assert calls == [(text, 7)] * 3
    assert all(s["text"] == shortened and s["tempo"] == 1 for s in result["segments"])
    with pytest.raises(tts.TtsError, match="narration 1 too long for clip window"):
        tts.build_narration(str(video), [text] * 3, "Charon", "Calm", "English", str(tmp_path), synth=synth)


def test_build_shortening_limit_and_empty(video, tmp_path):
    calls = []

    def shorten(text, max_words):
        calls.append(max_words)
        return text

    with pytest.raises(tts.TtsError, match="narration 1 too long"):
        tts.build_narration(str(video), ["too long"] * 3, "Charon", "Calm", "English", str(tmp_path),
                            synth=lambda *args: (wav_bytes(15), "fake"), shorten=shorten)
    assert len(calls) == tts.MAX_SHORTEN
    with pytest.raises(tts.TtsError, match="empty"):
        tts.build_narration(str(video), [" "], "Charon", "Calm", "English", str(tmp_path))


@pytest.mark.parametrize("with_audio", [True, False])
def test_mix(video, tmp_path, with_audio):
    source = video
    if not with_audio:
        source = tmp_path / "silent.mp4"
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-map", "0:v:0", "-c:v", "copy", str(source)],
                       check=True, capture_output=True, timeout=300)
    narration = tmp_path / "speech.wav"
    narration.write_bytes(wav_bytes(2))
    out = str(tmp_path / "mixed.mp4")
    assert tts.mix_narration(str(source), str(narration), out) == out
    assert Path(out).exists()
    before, after = probe(source), probe(out)
    assert [s["codec_type"] for s in after["streams"]] == ["video", "audio"]
    assert after["streams"][0]["codec_name"] == before["streams"][0]["codec_name"]
    assert after["streams"][1]["sample_rate"] == "48000"
    assert after["streams"][1]["codec_name"] == "aac"
    assert float(after["format"]["duration"]) == pytest.approx(float(before["format"]["duration"]), abs=0.1)


def audio_samples(path):
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "24000", "pipe:1"],
        check=True, capture_output=True, timeout=300,
    )
    return [sample for (sample,) in struct.iter_unpack("<h", result.stdout)]


def rms(samples, start, end):
    window = samples[round(start * 24000):round(end * 24000)]
    return math.sqrt(sum(sample * sample for sample in window) / len(window)) / 32768


def test_timing_ducking(video, tmp_path):
    source, silent, out = tmp_path / "bed.mp4", tmp_path / "silent.wav", tmp_path / "ducked.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-t", "8", "-c", "copy", str(source)],
                   check=True, capture_output=True, timeout=300)
    with wave.open(str(silent), "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(bytes(8 * 24000 * 2))
    tts.mix_narration(str(source), str(silent), str(out), segments=[{"start": 2.0, "duration": 3.0}])
    samples = audio_samples(out)
    full, ducked, recovered = [rms(samples, a, b) for a, b in [(0.5, 1.5), (2.5, 4.5), (6, 7)]]
    ramp = rms(samples, 1.84, 1.89)
    print(f"RMS full={full:.6f} ducked={ducked:.6f} recovered={recovered:.6f} ramp={ramp:.6f}; "
          f"duck={20 * math.log10(ducked / full):.2f} dB")
    assert 20 * math.log10(ducked / full) == pytest.approx(-12, abs=3)
    assert recovered == pytest.approx(full, rel=0.15)
    assert ramp > ducked


def test_duck_expression_overlap():
    assert tts.duck_expression([]) == "1"
    expression = tts.duck_expression([{"start": 2, "duration": 3}, {"start": 5.2, "duration": 1}])
    assert "\\," in expression

    def gain(t):
        return eval(expression.replace("\\,", ","), {"__builtins__": {}},
                    {"t": t, "min": min, "clip": lambda x, low, high: max(low, min(high, x))})

    for t, expected in [(0, 1), (1.8, 1), (1.9, 0.625), (2, 0.25), (5.2, 0.25), (7, 1)]:
        assert gain(t) == pytest.approx(expected)
    assert gain(5.1) == pytest.approx(0.25)
    with pytest.raises(tts.TtsError, match="invalid ducking"):
        tts.duck_expression([{"start": float("nan"), "duration": 1}])


def test_normalize_bed(video, tmp_path):
    quiet, output = tmp_path / "quiet.mp4", tmp_path / "normalized.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-t", "8", "-c:v", "copy",
                    "-af", "volume=0.01", "-c:a", "aac", str(quiet)],
                   check=True, capture_output=True, timeout=300)
    assert tts.normalize_bed(str(quiet), str(output)) == str(output)
    assert output.exists()
    assert float(probe(output)["format"]["duration"]) == pytest.approx(float(probe(quiet)["format"]["duration"]), abs=0.1)
    measurement = subprocess.run(
        ["ffmpeg", "-i", str(output), "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        check=True, capture_output=True, text=True, timeout=300,
    )
    stats, _ = json.JSONDecoder().raw_decode(measurement.stderr[measurement.stderr.rfind("{"):])
    loudness = stats["input_i"]
    assert float(loudness) == pytest.approx(-14, abs=2)
    assert rms(audio_samples(output), 1, 2) > rms(audio_samples(quiet), 1, 2) * 10
    no_audio = tmp_path / "no-audio.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-an", "-c:v", "copy", str(no_audio)],
                   check=True, capture_output=True, timeout=300)
    with pytest.raises(tts.TtsError, match="video has no audio"):
        tts.normalize_bed(str(no_audio), str(output))


def test_narrate_video_passes_segments(video, tmp_path, monkeypatch):
    wav = tmp_path / "narration.wav"
    wav.write_bytes(wav_bytes(1))
    segments = [{"start": 0.25, "duration": 1}]
    monkeypatch.setattr(tts, "build_narration", lambda *args, **kwargs: {
        "audio_path": str(wav), "model": "fake", "segments": segments,
    })
    calls = []
    monkeypatch.setattr(tts, "mix_narration", lambda *args, **kwargs: calls.append((args, kwargs)))
    monkeypatch.setattr("shutil.move", lambda *args: None)
    job = SimpleNamespace(video_path=str(video), narration={"voice": "Charon", "style": "Calm", "language": "English"},
                          clip_seconds=10, id=123)
    result = tts.narrate_video(job, ["One"], str(tmp_path))
    assert calls[0][1] == {"segments": segments}
    assert result["segments"] == segments


def burst_wav(durations, pauses=None):
    pauses = pauses if pauses is not None else [0.4 + 0.2 * i for i in range(len(durations) - 1)]
    frames = bytes(7200)
    for i, duration in enumerate(durations):
        with wave.open(io.BytesIO(wav_bytes(duration)), "rb") as wav:
            frames += wav.readframes(wav.getnframes())
        if i < len(pauses):
            frames += bytes(round(pauses[i] * 24000) * 2)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setparams((1, 2, 24000, 0, "NONE", "not compressed"))
        wav.writeframes(frames + bytes(9600))
    return buffer.getvalue()


def test_build_single_request(video, tmp_path):
    texts, calls = ["One two", "Three four five", "Six seven eight nine"], []

    def synth(text, *args):
        calls.append(text)
        return burst_wav([2, 3, 4]), "fake"

    result = tts.build_narration(str(video), texts, "Charon", "Calm", "English", str(tmp_path), synth=synth)
    assert calls == ["\n\n".join(texts)]
    assert result["requests"] == 1 and result["mode"] == "single"
    assert [s["duration"] for s in result["segments"]] == pytest.approx([2, 3, 4], abs=0.01)
    assert [s["start"] for s in result["segments"]] == pytest.approx([0.25, 10.15, 20.05])
    samples = audio_samples(result["audio_path"])
    for start in [0.25, 10.15, 20.05]:
        assert rms(samples, start, start + 0.05) > 0.1
        assert rms(samples, start - 0.1, start - 0.05) == 0


@pytest.mark.parametrize("durations,pauses", [([2, 2, 2], [0.1, 0.1]), ([1, 1, 6], None), ([0.5, 0.5, 0.5], None)])
def test_build_single_fallback(video, tmp_path, caplog, durations, pauses):
    calls = []

    def synth(text, *args):
        calls.append(text)
        return (burst_wav(durations, pauses) if len(calls) == 1 else wav_bytes(2)), "fake"

    result = tts.build_narration(str(video), ["One", "Two", "Three"], "Charon", "Calm", "English", str(tmp_path), synth=synth)
    assert len(calls) == result["requests"] == 4
    assert result["mode"] == "per_clip"
    assert caplog.messages.count("single-request TTS split failed, per-clip fallback") == 1


def test_single_request_shortens_only_long_part(video, tmp_path):
    texts = ["one two three four five six seven eight nine ten", "two words", "three words"]
    calls, shortened = [], []

    def synth(text, *args):
        calls.append(text)
        return (burst_wav([12, 2.4, 2.4]) if len(calls) == 1 else wav_bytes(6)), "fake"

    def shorten(text, words):
        shortened.append((text, words))
        return "one two three four five six"

    result = tts.build_narration(str(video), texts, "Charon", "Calm", "English", str(tmp_path), synth=synth, shorten=shorten)
    assert calls == ["\n\n".join(texts), "one two three four five six"]
    assert result["requests"] == 2 and result["mode"] == "single"
    assert shortened == [(texts[0], 7)]
    assert [s["text"] for s in result["segments"]][1:] == texts[1:]


def test_split_one_sentence_and_silence():
    parts = tts.split_by_pauses(burst_wav([2]), [2])
    assert len(parts) == 1 and tts._wav_duration(parts[0]) == pytest.approx(2, abs=0.01)
    assert tts.split_by_pauses(burst_wav([2]), [0]) is None
    assert tts.split_by_pauses(burst_wav([2]), [1, 1]) is None


def test_every_ffmpeg_output_is_time_bounded(monkeypatch, tmp_path):
    """A runaway apad once wrote an 8 GB WAV and filled the disk: outputs must carry -t and padding must be bounded."""
    calls = []

    def fake_run(args, timeout=300):
        calls.append(args)
        if args[-1].endswith(".raw"):
            Path(args[-1]).write_bytes(b"\0" * 4)
        class Result:
            stdout = '{"format": {"duration": "30.0"}, "streams": [{"codec_type": "video"}, {"codec_type": "audio", "duration": "30.0"}]}'
            stderr = '{"input_i": "-20", "input_tp": "-3", "input_lra": "5", "input_thresh": "-30", "target_offset": "0"}'
        return Result()

    monkeypatch.setattr(tts, "_run", fake_run)
    segments = [{"index": 1, "text": "a", "start": 0.25, "duration": 2.0, "tempo": 1.0}]
    tts._render_track([tmp_path / "1.wav"], segments, 30.0, tmp_path / "n.wav")
    tts.mix_narration(str(tmp_path / "v.mp4"), str(tmp_path / "n.wav"), str(tmp_path / "o.mp4"), segments=segments)
    tts.normalize_bed(str(tmp_path / "v.mp4"), str(tmp_path / "b.mp4"))
    ffmpeg_calls = [c for c in calls if c[0] == "ffmpeg"]
    assert ffmpeg_calls
    for args in ffmpeg_calls:
        assert "-t" in args, args
        assert "apad," not in " ".join(args) and not " ".join(args).rstrip().endswith("apad"), args
