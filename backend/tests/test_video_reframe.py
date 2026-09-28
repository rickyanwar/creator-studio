"""Unit tests for S2g — video_reframe.py encoder settings and output size.

Tests run without ffmpeg — we only check the generated command list and
the pure output_size() function.
pytest -q backend/tests/test_video_reframe.py
"""

import pytest
from pathlib import Path
from app.services.video_reframe import _encoder_cmd, output_size, ClipSpec, OUT_W, OUT_H


# ── output_size ───────────────────────────────────────────────────────────────

class TestOutputSize:
    """S2g rule: out_h = even(min(1920, max(960, src_h))), out_w = even(round(out_h*9/16))."""

    def test_2160p_capped_at_1920(self):
        w, h = output_size(2160)
        assert h == 1920
        assert w == 1080   # round(1920*9/16) = 1080

    def test_1440p_no_cap(self):
        w, h = output_size(1440)
        assert h == 1440
        assert w == 810    # round(1440*9/16) = 810

    def test_1080p(self):
        w, h = output_size(1080)
        assert h == 1080
        # round(1080 * 9 / 16) = round(607.5) = 608
        assert w == 608

    def test_720p_minimum(self):
        # 720 < 960 minimum → out_h = 960
        w, h = output_size(720)
        assert h == 960
        assert w == 540    # round(960*9/16) = 540

    def test_480p_minimum(self):
        # 480 < 960 minimum → out_h = 960
        w, h = output_size(480)
        assert h == 960
        assert w == 540

    def test_even_dimensions(self):
        # All outputs must be even (required by yuv420p)
        for src_h in [480, 720, 1080, 1440, 2160, 961, 1081]:
            w, h = output_size(src_h)
            assert w % 2 == 0, f"w={w} not even for src_h={src_h}"
            assert h % 2 == 0, f"h={h} not even for src_h={src_h}"

    def test_never_upscales_above_source(self):
        # For any source height ≥ 960, out_h must never exceed src_h.
        for src_h in [960, 1000, 1080, 1440, 1920, 2160]:
            _, h = output_size(src_h)
            assert h <= src_h or h == 960, f"upscaled for src_h={src_h}: got h={h}"

    def test_exact_1920_source_stays_1920(self):
        w, h = output_size(1920)
        assert h == 1920
        assert w == 1080


# ── _encoder_cmd ──────────────────────────────────────────────────────────────

def _make_spec(out_w=OUT_W, out_h=OUT_H) -> ClipSpec:
    return ClipSpec(
        src=Path("/tmp/src.mp4"),
        seek_s=0.0,
        duration_s=60.0,
        out=Path("/tmp/out.mp4"),
        out_w=out_w,
        out_h=out_h,
    )


class TestEncoderCmd:
    """S2g encoder settings: CRF 22, preset medium, 128k, faststart."""

    def _cmd(self, **kwargs) -> list[str]:
        return _encoder_cmd(_make_spec(**kwargs))

    def test_crf_22(self):
        cmd = self._cmd()
        assert "-crf" in cmd
        idx = cmd.index("-crf")
        assert cmd[idx + 1] == "22"

    def test_preset_medium(self):
        cmd = self._cmd()
        assert "-preset" in cmd
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "medium"

    def test_profile_high(self):
        cmd = self._cmd()
        assert "-profile:v" in cmd
        idx = cmd.index("-profile:v")
        assert cmd[idx + 1] == "high"

    def test_level_4_1(self):
        cmd = self._cmd()
        assert "-level:v" in cmd
        idx = cmd.index("-level:v")
        assert cmd[idx + 1] == "4.1"

    def test_audio_128k(self):
        cmd = self._cmd()
        assert "-b:a" in cmd
        idx = cmd.index("-b:a")
        assert cmd[idx + 1] == "128k"

    def test_faststart(self):
        cmd = self._cmd()
        assert "-movflags" in cmd
        idx = cmd.index("-movflags")
        assert "+faststart" in cmd[idx + 1]

    def test_no_fixed_video_bitrate(self):
        # S2g: quality-based, no -b:v or -maxrate
        cmd = self._cmd()
        assert "-b:v" not in cmd
        assert "-maxrate" not in cmd

    def test_size_flag_matches_spec(self):
        cmd = _encoder_cmd(_make_spec(out_w=810, out_h=1440))
        assert "-s" in cmd
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "810x1440"

    def test_default_size_1080x1920(self):
        cmd = self._cmd()
        assert "-s" in cmd
        idx = cmd.index("-s")
        assert cmd[idx + 1] == "1080x1920"

    def test_no_shortest_flag(self):
        # -shortest caused ~900 MB extra RAM (measured 2026-09-25)
        cmd = self._cmd()
        assert "-shortest" not in cmd

    def test_libx264_codec(self):
        cmd = self._cmd()
        assert "libx264" in cmd

    def test_yuv420p_pixel_format(self):
        cmd = self._cmd()
        assert "-pix_fmt" in cmd
        idx = cmd.index("-pix_fmt")
        assert cmd[idx + 1] == "yuv420p"

    def test_48khz_sample_rate(self):
        cmd = self._cmd()
        assert "-ar" in cmd
        idx = cmd.index("-ar")
        assert cmd[idx + 1] == "48000"
