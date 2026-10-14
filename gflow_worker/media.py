import json
import os
from pathlib import Path
import subprocess


class Media:
    def __init__(self, cfg):
        self.ffmpeg, self.ffprobe = cfg.ffmpeg, cfg.ffprobe

    def _run(self, args):
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=600)

    def probe(self, path):
        return json.loads(self._run([self.ffprobe, "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]).stdout)

    def probe_duration(self, path):
        return float(self.probe(path)["format"]["duration"])

    def last_frame(self, clip, png, seed_offset_ms):
        offset = max(seed_offset_ms / 1000, 0.001)
        png = Path(png)
        png.unlink(missing_ok=True)
        try:
            self._run([self.ffmpeg, "-y", "-sseof", f"-{offset}", "-i", str(clip), "-frames:v", "1", "-update", "1", str(png)])
        except subprocess.CalledProcessError:
            pass
        if not png.exists() or not png.stat().st_size:
            self._run([self.ffmpeg, "-y", "-ss", str(max(0, self.probe_duration(clip) - offset)), "-i", str(clip),
                       "-frames:v", "1", "-update", "1", str(png)])
        if not png.exists() or not png.stat().st_size:
            raise ValueError("could not extract seed frame")
        return png

    def concat(self, clips, out, trim_tail_ms=0):
        args = [self.ffmpeg, "-y"]
        filters, labels = [], []
        stream_index = 0
        for i, clip in enumerate(clips):
            info = self.probe(clip)
            args += ["-i", str(clip)]
            video_index = stream_index
            stream_index += 1
            end = max(0.1, float(info["format"]["duration"]) - trim_tail_ms / 1000) if (trim_tail_ms > 0 and i < len(clips) - 1) else None
            if any(s["codec_type"] == "audio" for s in info["streams"]):
                audio_index = video_index
            else:
                dur = f"{end:.3f}" if end is not None else info["format"]["duration"]
                args += ["-f", "lavfi", "-t", dur, "-i", "anullsrc=r=48000:cl=stereo"]
                audio_index = stream_index
                stream_index += 1
            v_filt = f"trim=end={end:.3f},setpts=PTS-STARTPTS" if end is not None else "setpts=PTS-STARTPTS"
            a_filt = (f"atrim=end={end:.3f},aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS"
                      if end is not None else
                      "aresample=48000,aformat=channel_layouts=stereo,asetpts=PTS-STARTPTS")
            filters += [f"[{video_index}:v:0]{v_filt}[v{i}]",
                        f"[{audio_index}:a:0]{a_filt}[a{i}]"]
            labels.append(f"[v{i}][a{i}]")
        filters.append("".join(labels) + f"concat=n={len(clips)}:v=1:a=1[v][a]")
        self._run([*args, "-filter_complex", ";".join(filters), "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
                   "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
                   "-movflags", "+faststart", str(out)])

    def thumbnail(self, video, jpg):
        self._run([self.ffmpeg, "-y", "-ss", "1.0", "-i", str(video), "-frames:v", "1", "-vf", "scale=720:-1", "-update", "1", str(jpg)])
        if not Path(jpg).exists():
            self._run([self.ffmpeg, "-y", "-i", str(video), "-frames:v", "1", "-vf", "scale=720:-1", "-update", "1", str(jpg)])

    def reencode_for_upload(self, video, duration):
        video = Path(video)
        target_bytes = 38 * 1024 * 1024
        video_bitrate = max(500_000, int(target_bytes * 8 / duration) - 128_000)
        reencoded = video.with_name(f".{video.stem}.upload.mp4")
        self._run([self.ffmpeg, "-y", "-i", str(video), "-c:v", "libx264", "-preset", "medium",
                   "-b:v", str(video_bitrate), "-maxrate", str(video_bitrate),
                   "-bufsize", str(video_bitrate * 2), "-c:a", "aac", "-b:a", "128k",
                   "-movflags", "+faststart", str(reencoded)])
        os.replace(reencoded, video)
